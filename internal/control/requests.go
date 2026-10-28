package control

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"strconv"
	"time"
	"unicode/utf8"
)

const maxResponseBytes = 2 * 1024 * 1024
const maxResultBytes = 16 * 1024 * 1024

type requestResult struct {
	owner, permission    string
	data                 json.RawMessage
	expires              time.Time
	inspect              *inspectRequest
	aspect               *aspectRequest
	notificationSettings *notificationsRequest
	playerSettings       *playerSettingsRequest
	plexFolders          *plexFoldersRequest
	settingsPatch        *settingsPatchRequest
	perfCapture          *perfCaptureRequest
	vportalSettings      *vportalSettingsRequest
	category             *categoryRequest
	randomPlay           *randomPlayRequest
	plexLibrary          *plexLibraryRequest
	localMedia           *localMediaRequest
	favorites            *favoritesRequest
	favoritesTransfer    *favoritesTransferRequest
	handoff              *handoffRequest
	offlineProfile       *offlineProfileRequest
}

// Requests are an opt-in extension to the v1 command wire contract. Legacy
// clients ignore them, and the CLI times out rather than claiming execution.
func (s *Server) requests(w http.ResponseWriter, r *http.Request, d *device, now time.Time, route string) {
	q, ok := query(r, "device_id", "id")
	if !ok || (r.Method == "POST" && q.Has("id")) {
		failure(w, 400, "unsupported query parameters")
		return
	}
	if d == nil {
		for _, candidate := range s.currentDevices() {
			if candidate.id == q.Get("device_id") {
				d = candidate
			}
		}
		if d == nil {
			failure(w, 400, "a configured device_id is required")
			return
		}
	} else if q.Has("device_id") && q.Get("device_id") != d.id {
		failure(w, 403, "device_id does not match credentials")
		return
	}
	if o := operatorFor(r); o != nil && !o.devices[d.id] {
		failure(w, 403, "device scope is not allowed")
		return
	}
	// Use the same canonical route that selected the credential role. Re-parsing
	// a trailing-slash variant must never let a device become an RPC submitter.
	if route == "/api/responses" {
		s.receiveResult(w, r, d)
		return
	}
	if r.Method == "GET" {
		if !commandID.MatchString(q.Get("id")) {
			failure(w, 400, "a request id is required")
			return
		}
		s.mu.Lock()
		s.expire(now)
		result, found := d.results[q.Get("id")]
		pending := false
		permitted := !found || ownResult(r, result.owner, result.permission)
		for _, e := range d.queue {
			if e.rpc && e.id == q.Get("id") {
				pending = true
				permitted = ownResult(r, e.owner, e.permission)
			}
		}
		s.mu.Unlock()
		if !permitted {
			failure(w, 403, "request scope is not allowed")
			return
		}
		if found {
			// The envelope was already validated and bounded on receipt. Encoding
			// it again expands HTML characters and can exceed the same wire limit.
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write(result.data)
		} else if pending {
			reply(w, 202, map[string]string{"status": "pending"})
		} else {
			failure(w, 404, "request expired or not found")
		}
		return
	}
	b, ok := readBody(w, r)
	if !ok {
		return
	}
	m, err := decodeObject(b)
	var action, key string
	if raw, exists := m["idempotency_key"]; exists {
		if json.Unmarshal(raw, &key) != nil || !operationKey.MatchString(key) {
			failure(w, 400, "invalid operation key")
			return
		}
		delete(m, "idempotency_key")
	}
	if err != nil || len(m) != 2 || json.Unmarshal(m["action"], &action) != nil {
		failure(w, 400, "action and params are required")
		return
	}
	params, err := decodeObject(m["params"])
	if err != nil {
		failure(w, 400, "params must be an object")
		return
	}
	var favoritesTransferBound *favoritesTransferRequest
	switch action {
	case "kiosk":
		var mode string
		ok = json.Unmarshal(params["mode"], &mode) == nil
		switch mode {
		case "status", "off":
			ok = ok && len(params) == 1
		case "on", "set":
			_, validQuery := textValue(params["query"], 1024)
			count := len(params)
			if raw, exists := params["strict"]; exists {
				count--
				var strict *bool
				ok = ok && json.Unmarshal(raw, &strict) == nil && strict != nil
			}
			ok = ok && ((mode == "on" && count == 1) || (count == 2 && validQuery && profileText(params["query"], 1024)))
		default:
			ok = false
		}
	case "status", "providers", "profiles", "epg_catalog":
		ok = len(params) == 0
	case "capabilities":
		ok = validLocalMediaCapabilityProbe(params)
	case "screenshot":
		ok = validScreenshotRequest(params)
	case "aspect":
		ok = parseAspectRequest(params) != nil
	case "notifications":
		ok = parseNotificationsRequest(params) != nil
	case "player_settings":
		ok = parsePlayerSettingsRequest(params) != nil
	case "plex_folders":
		ok = parsePlexFoldersRequest(params) != nil
	case "player_settings_patch":
		ok = parseSettingsPatchRequest(params) != nil
	case "perf_capture":
		ok = parsePerfCaptureRequest(params) != nil
	case "categories":
		ok = parseCategoryRequest(action, params) != nil
	case "inspect":
		ok = parseInspectRequest(params) != nil
	case "plex_queue":
		ok = parsePlexQueueRequest(params) != nil
	case "local_media":
		ok = parseLocalMediaRequest(params) != nil
	case "plex_library":
		ok = parsePlexLibraryRequest(params) != nil
	case "favorites":
		ok = parseFavoritesRequest(params) != nil
	case "favorites_transfer":
		favoritesTransferBound = parseFavoritesTransferRequest(params)
		ok = favoritesTransferBound != nil
	case "app_update":
		ok = validAppUpdateRequest(params)
	case "maintenance":
		ok = validMaintenanceRequest(params)
	case "vportal_queue":
		ok = validVPortalQueueRequest(params)
	case "lifecycle", "input", "playback":
		ok = validControlRequest(action, params)
	case "profile", "profile_export":
		ok = len(params) == 1 && profileInteger(params["number"], 1, 15)
	case "profile_save":
		ok = profileInteger(params["number"], 1, 15) && (len(params) == 1 || (len(params) == 2 && profileText(params["name"], 256)))
	case "profile_import":
		ok = len(params) == 2 && profileInteger(params["number"], 1, 15) && validOfflineProfile(params["profile"])
	case "profile_settings":
		ok = len(params) == 2 && profileInteger(params["number"], 1, 15) && validProfileSettings(params["settings"])
	case "settings_compare":
		ok = validSettingsCompare(params)
	case "playback_handoff_export", "playback_handoff_import":
		ok = parseHandoffRequest(action, params) != nil
	case "restart":
		var target string
		ok = len(params) == 1 && json.Unmarshal(params["target"], &target) == nil && (target == "stream" || target == "player")
	case "play_catalog":
		_, validCatalog := textValue(params["catalog"], 128)
		_, validID := textValue(params["id"], 2048)
		ok = len(params) == 2 && validCatalog && validID
		if _, random := params["random"]; random {
			ok = parseRandomPlayRequest(params) != nil
		}
	case "resolve_archive", "play_archive_catalog":
		_, validCatalog := textValue(params["catalog"], 128)
		_, validID := textValue(params["id"], 2048)
		_, validTitle := textValue(params["title"], 65536)
		var start, end float64
		validTimes := numberValue(params["start"], 1, 9007199254740991, true) &&
			numberValue(params["end"], 1, 9007199254740991, true)
		if validTimes {
			_ = json.Unmarshal(params["start"], &start)
			_ = json.Unmarshal(params["end"], &end)
		}
		ok = len(params) == 5 && validCatalog && validID && validTitle && validTimes && start < end
	case "play", "provider":
		var value string
		ok = len(params) == 1 && json.Unmarshal(params["query"], &value) == nil && value != "" && len(value) <= 1024
	case "vportal", "vportal_random", "vportal_search":
		_, validQuery := textValue(params["query"], 1024)
		ok = len(params) == 1 && validQuery
	case "channels", "programs":
		ok = len(params) == 0
		if len(params) == 1 {
			var search string
			ok = json.Unmarshal(params["search"], &search) == nil && len(search) <= 1024
		}
		if action == "channels" && !ok {
			ok = parseCategoryRequest(action, params) != nil
		}
	case "command":
		_, err = validateCommand(m["params"])
		ok = err == nil
	case "provider_settings":
		// Provider-specific validation and parental/distribution policy live in
		// the player. No credentials are returned by status or provider listing.
		var provider string
		_, fieldsErr := decodeObject(params["settings"])
		ok = len(params) == 2 && json.Unmarshal(params["provider"], &provider) == nil && fieldsErr == nil && (provider == "m3u" || provider == "xtream" || provider == "stalker" || provider == "ottclub" || provider == "plex")
		if settingString(params["provider"]) == "vportal" {
			ok = parseVPortalSettingsRequest(action, params) != nil
		}
	default:
		ok = false
	}
	if !ok {
		failure(w, 400, "invalid request action or parameters")
		return
	}
	permission := commandAction(action, params)
	if !allowedCommand(r, d, permission) {
		failure(w, 403, "command scope is not allowed")
		return
	}
	var random [16]byte
	if _, err = rand.Read(random[:]); err != nil {
		failure(w, 503, "cannot create request identifier")
		return
	}
	id := hex.EncodeToString(random[:])
	canonical, _ := json.Marshal(m)
	if action == "capabilities" && len(params) != 0 {
		// The probe negotiates relay support, not a new player operation. Keep
		// the original params in the idempotency hash before forwarding the
		// legacy empty-params request understood by every player version.
		m["params"] = json.RawMessage(`{}`)
	}
	now = s.now()
	expires := now.Add(s.ttl)
	m["id"], _ = json.Marshal(id)
	m["expires_at"], _ = json.Marshal(float64(expires.UnixNano()) / 1e9)
	data, _ := json.Marshal(m)
	if action == "favorites_transfer" && !validFavoritesTransferEnvelope(data) {
		// Check the actual queued bytes after transport IDs/expiry are added,
		// before the idempotency journal or queue can be changed.
		failure(w, 413, "favorites transfer envelope is too large or invalid")
		return
	}
	s.mu.Lock()
	s.expire(now)
	if (len(d.queue) >= s.maxPending || s.bytes+len(data) > MaxQueueBytes || len(d.results) >= 50) && !s.hasOperation(r, key, now) {
		s.mu.Unlock()
		failure(w, 429, "request queue is full")
		return
	}
	if key != "" {
		replay, ok := s.startOperation(w, r, d, permission, key, id, canonical, now, expires)
		if replay || !ok {
			s.mu.Unlock()
			return
		}
	}
	e := entry{id: id, data: data, expires: expires, rpc: true, owner: requestOwner(r), permission: permission}
	if action == "screenshot" {
		_ = json.Unmarshal(params["runtime"], &e.screenshotRuntime)
	}
	if action == "inspect" {
		e.inspect = parseInspectRequest(params)
	}
	if action == "aspect" {
		e.aspect = parseAspectRequest(params)
	}
	if action == "notifications" {
		e.notificationSettings = parseNotificationsRequest(params)
	}
	if action == "player_settings" {
		e.playerSettings = parsePlayerSettingsRequest(params)
	}
	if action == "plex_folders" {
		e.plexFolders = parsePlexFoldersRequest(params)
	}
	if action == "player_settings_patch" {
		e.settingsPatch = parseSettingsPatchRequest(params)
	}
	if action == "perf_capture" {
		e.perfCapture = parsePerfCaptureRequest(params)
	}
	if (action == "provider_settings" || action == "settings_compare") && settingString(params["provider"]) == "vportal" {
		e.vportalSettings = parseVPortalSettingsRequest(action, params)
	}
	if action == "categories" || action == "channels" {
		e.category = parseCategoryRequest(action, params)
	}
	if action == "play_catalog" {
		e.randomPlay = parseRandomPlayRequest(params)
	}
	if action == "plex_queue" {
		e.plexQueue = parsePlexQueueRequest(params)
	}
	if action == "local_media" {
		e.localMedia = parseLocalMediaRequest(params)
	}
	if action == "plex_library" {
		e.plexLibrary = parsePlexLibraryRequest(params)
	}
	if action == "favorites" {
		e.favorites = parseFavoritesRequest(params)
	}
	if action == "favorites_transfer" {
		e.favoritesTransfer = favoritesTransferBound
	}
	if action == "profile_save" || action == "profile_export" || action == "profile_import" {
		var number int
		_ = json.Unmarshal(params["number"], &number)
		e.offlineProfile = &offlineProfileRequest{Action: action, Number: number}
	}
	if action == "playback_handoff_export" || action == "playback_handoff_import" {
		e.handoff = parseHandoffRequest(action, params)
	}
	if action == "playback" {
		var operation string
		_ = json.Unmarshal(params["operation"], &operation)
		if operation == "previous_channel" || operation == "next_channel" {
			e.playbackOperation = operation
		}
	}
	d.queue = append(d.queue, e)
	s.bytes += len(data)
	s.mu.Unlock()
	reply(w, 202, map[string]string{"status": "queued", "id": id})
}

func profileInteger(raw json.RawMessage, min, max int) bool {
	var value *int
	return json.Unmarshal(raw, &value) == nil && value != nil && *value >= min && *value <= max
}

func profileText(raw json.RawMessage, max int) bool {
	var value string
	if len(raw) < 2 || raw[0] != '"' || !utf8.Valid(raw) || json.Unmarshal(raw, &value) != nil || len(value) > max {
		return false
	}
	for _, character := range value {
		if character < 32 || character == 127 {
			return false
		}
	}
	// encoding/json replaces unpaired UTF-16 surrogates. Reject them instead of
	// silently changing a profile name or credential-bearing URL in transit.
	for i := 1; i < len(raw)-1; i++ {
		if raw[i] != '\\' {
			continue
		}
		i++
		if raw[i] != 'u' {
			continue
		}
		code, _ := strconv.ParseUint(string(raw[i+1:i+5]), 16, 16)
		i += 4
		if code >= 0xD800 && code <= 0xDBFF {
			if i+6 >= len(raw) || raw[i+1] != '\\' || raw[i+2] != 'u' {
				return false
			}
			low, err := strconv.ParseUint(string(raw[i+3:i+7]), 16, 16)
			if err != nil || low < 0xDC00 || low > 0xDFFF {
				return false
			}
			i += 6
		} else if code >= 0xDC00 && code <= 0xDFFF {
			return false
		}
	}
	return true
}

func validProfileSettings(raw json.RawMessage) bool {
	settings, err := decodeObject(raw)
	if err != nil || len(settings) == 0 {
		return false
	}
	for key, value := range settings {
		switch key {
		case "name":
			if !profileText(value, 256) {
				return false
			}
		case "playlist", "vportal":
			if !profileText(value, 8192) {
				return false
			}
		case "history_hours":
			if !profileInteger(value, 0, 8760) {
				return false
			}
		default:
			return false
		}
	}
	return true
}

func (s *Server) receiveResult(w http.ResponseWriter, r *http.Request, d *device) {
	b, ok := readLimitedBody(w, r, maxResponseBytes)
	if !ok {
		return
	}
	m, err := decodeObject(b)
	var id, status string
	if err != nil || len(m) != 3 || json.Unmarshal(m["id"], &id) != nil || !commandID.MatchString(id) || json.Unmarshal(m["status"], &status) != nil || (status != "ok" && status != "rejected" && status != "unsupported") || m["data"] == nil {
		failure(w, 400, "invalid result envelope")
		return
	}
	now := s.now()
	// Decode image data outside the shared queue lock. The normal result path
	// below rechecks expiry and ownership before storing this validated result.
	s.mu.Lock()
	screenshotRuntime := ""
	var plexQueue *plexQueueRequest
	var plexLibrary *plexLibraryRequest
	var localMedia *localMediaRequest
	var favorites *favoritesRequest
	var favoritesTransfer *favoritesTransferRequest
	var handoff *handoffRequest
	var offlineProfile *offlineProfileRequest
	var inspect *inspectRequest
	var aspect *aspectRequest
	var notificationSettings *notificationsRequest
	var playerSettings *playerSettingsRequest
	var plexFolders *plexFoldersRequest
	var settingsPatch *settingsPatchRequest
	var perfCapture *perfCaptureRequest
	capabilitiesResult := d.results[id].permission == "capabilities"
	var vportalSettings *vportalSettingsRequest
	var category *categoryRequest
	var randomPlay *randomPlayRequest
	playbackOperation := ""
	for _, pending := range d.queue {
		if pending.rpc && pending.id == id {
			screenshotRuntime = pending.screenshotRuntime
			plexQueue = pending.plexQueue
			plexLibrary = pending.plexLibrary
			localMedia = pending.localMedia
			favorites = pending.favorites
			favoritesTransfer = pending.favoritesTransfer
			handoff = pending.handoff
			offlineProfile = pending.offlineProfile
			playbackOperation = pending.playbackOperation
			inspect = pending.inspect
			aspect = pending.aspect
			notificationSettings = pending.notificationSettings
			playerSettings = pending.playerSettings
			plexFolders = pending.plexFolders
			settingsPatch = pending.settingsPatch
			perfCapture = pending.perfCapture
			capabilitiesResult = pending.permission == "capabilities"
			vportalSettings = pending.vportalSettings
			category = pending.category
			randomPlay = pending.randomPlay
			break
		}
	}
	if inspect == nil {
		inspect = d.results[id].inspect
	}
	if aspect == nil {
		aspect = d.results[id].aspect
	}
	if notificationSettings == nil {
		notificationSettings = d.results[id].notificationSettings
	}
	if playerSettings == nil {
		playerSettings = d.results[id].playerSettings
	}
	if plexFolders == nil {
		plexFolders = d.results[id].plexFolders
	}
	if settingsPatch == nil {
		settingsPatch = d.results[id].settingsPatch
	}
	if perfCapture == nil {
		perfCapture = d.results[id].perfCapture
	}
	if vportalSettings == nil {
		vportalSettings = d.results[id].vportalSettings
	}
	if category == nil {
		category = d.results[id].category
	}
	if randomPlay == nil {
		randomPlay = d.results[id].randomPlay
	}
	if localMedia == nil {
		localMedia = d.results[id].localMedia
	}
	if plexLibrary == nil {
		plexLibrary = d.results[id].plexLibrary
	}
	if favorites == nil {
		favorites = d.results[id].favorites
	}
	if favoritesTransfer == nil {
		favoritesTransfer = d.results[id].favoritesTransfer
	}
	if offlineProfile == nil {
		offlineProfile = d.results[id].offlineProfile
	}
	if handoff == nil {
		handoff = d.results[id].handoff
	}
	s.mu.Unlock()
	// Both successful and negative inspection replies must belong to the exact
	// requested page. An older or unrelated queue consumer cannot close the
	// request with an unbound unsupported response. This is result fencing, not
	// routing: a protocol-1 device queue still supports only one consumer.
	if perfCapture != nil && (len(b) > maxPerfOuterBytes || !validPerfCaptureResult(m["data"], status, perfCapture)) {
		failure(w, 400, "invalid performance capture result")
		return
	}
	if capabilitiesResult && status == "ok" {
		capabilities, err := decodeObject(m["data"])
		if err != nil {
			failure(w, 400, "invalid capabilities result")
			return
		}
		if marker, exists := capabilities["local_media"]; exists && !validLocalMediaCapability(marker) {
			failure(w, 400, "invalid local media capability")
			return
		}
		if marker, exists := capabilities["favorites_transfer"]; exists && !validFavoritesTransferCapability(marker) {
			failure(w, 400, "invalid favorites transfer capability")
			return
		}
		if marker, exists := capabilities["favorites_transfer_recovery"]; exists && !validFavoritesRecoveryCapability(marker) {
			failure(w, 400, "invalid favorites recovery capability")
			return
		}
		if marker, exists := capabilities["local_media_progressive"]; exists && (!validLocalMediaExtensionCapability(marker) || !validLocalMediaCapability(capabilities["local_media"])) {
			failure(w, 400, "invalid progressive local media capability")
			return
		}
		if marker, exists := capabilities["local_media_session"]; exists && (!validLocalMediaExtensionCapability(marker) || !validLocalMediaCapability(capabilities["local_media"])) {
			failure(w, 400, "invalid session local media capability")
			return
		}
		if marker, exists := capabilities["perf_capture"]; exists && !validPerfCapability(marker) {
			failure(w, 400, "invalid performance capture capability")
			return
		}
		if marker, exists := capabilities["perf_capture_hls"]; exists && (!validPerfHLSCapability(marker) || !validPerfCapability(capabilities["perf_capture"])) {
			failure(w, 400, "invalid HLS performance capture capability")
			return
		}
		if marker, exists := capabilities["perf_capture_hls_policy"]; exists && (!validPerfHLSPolicyCapability(marker) || !validPerfCapability(capabilities["perf_capture"])) {
			failure(w, 400, "invalid HLS policy performance capture capability")
			return
		}
	}
	if inspect != nil && (len(b) > maxInspectResultBytes || !validInspectResult(m["data"], status, inspect)) {
		failure(w, 400, "invalid inspect result")
		return
	}
	// Every aspect response belongs to the requested runtime and operation.
	// An unbound or stale negative response must not consume another page's work.
	if aspect != nil && !validAspectResult(m["data"], status, aspect) {
		failure(w, 400, "invalid aspect result")
		return
	}
	if notificationSettings != nil && (len(m["data"]) > 1024 || !validNotificationsResult(m["data"], status, notificationSettings)) {
		failure(w, 400, "invalid notifications result")
		return
	}
	if playerSettings != nil && !validPlayerSettingsResult(m["data"], status, playerSettings) {
		failure(w, 400, "invalid player settings result")
		return
	}
	if plexFolders != nil && !validPlexFoldersResult(m["data"], status, plexFolders) {
		failure(w, 400, "invalid Plex folder settings result")
		return
	}
	if settingsPatch != nil && !validSettingsPatchResult(m["data"], status, settingsPatch) {
		failure(w, 400, "invalid grouped settings result")
		return
	}
	if vportalSettings != nil && !validVPortalSettingsResult(m["data"], status, vportalSettings) {
		failure(w, 400, "invalid VPortal settings result")
		return
	}
	if category != nil && !validCategoryResult(m["data"], status, category) {
		failure(w, 400, "invalid category result")
		return
	}
	if randomPlay != nil && !validRandomPlayResult(m["data"], status, randomPlay) {
		failure(w, 400, "invalid random playback result")
		return
	}
	if screenshotRuntime != "" && status == "ok" && !validScreenshotResult(m["data"], screenshotRuntime) {
		failure(w, 400, "invalid screenshot result")
		return
	}
	if plexQueue != nil && (len(m["data"]) > maxPlexPreviewResultBytes || (status == "ok" && !validPlexQueueResult(m["data"], plexQueue))) {
		failure(w, 400, "invalid Plex queue result")
		return
	}
	if localMedia != nil && !validLocalMediaResult(m["data"], status, localMedia) {
		failure(w, 400, "invalid local media result")
		return
	}
	if plexLibrary != nil && !validPlexLibraryResult(m["data"], status, plexLibrary) {
		failure(w, 400, "invalid Plex library result")
		return
	}
	if favorites != nil && !validFavoritesResult(m["data"], status, favorites) {
		failure(w, 400, "invalid favorites result")
		return
	}
	if favoritesTransfer != nil && !validFavoritesTransferResult(m["data"], status, favoritesTransfer) {
		failure(w, 400, "invalid favorites transfer result")
		return
	}
	if offlineProfile != nil && !validOfflineProfileResult(m["data"], status, offlineProfile) {
		failure(w, 400, "invalid Local videos profile result")
		return
	}
	if handoff != nil && !validHandoffResult(m["data"], status, handoff) {
		failure(w, 400, "invalid handoff result")
		return
	}
	if playbackOperation != "" && status == "ok" && !validPlexPlaybackResult(m["data"], playbackOperation) {
		failure(w, 400, "invalid Plex playback result")
		return
	}
	now = s.now()
	s.mu.Lock()
	s.expire(now)
	if _, exists := d.results[id]; exists {
		s.mu.Unlock()
		reply(w, 200, map[string]string{"status": "ok"})
		return
	}
	for i, e := range d.queue {
		if !e.rpc || e.id != id {
			continue
		}
		if s.resultBytes+len(b) > maxResultBytes || len(d.results) >= 50 {
			s.mu.Unlock()
			failure(w, 429, "result storage is full")
			return
		}
		if err := s.journal.finish(map[string]bool{id: true}, "responded", status, now); err != nil {
			s.mu.Unlock()
			failure(w, 503, "command outcome could not be retained")
			return
		}
		if d.results == nil {
			d.results = make(map[string]requestResult)
		}
		d.results[id] = requestResult{owner: e.owner, permission: e.permission, data: b, expires: now.Add(60 * time.Second), inspect: e.inspect, aspect: e.aspect, notificationSettings: e.notificationSettings, playerSettings: e.playerSettings, plexFolders: e.plexFolders, settingsPatch: e.settingsPatch, perfCapture: e.perfCapture, vportalSettings: e.vportalSettings, category: e.category, randomPlay: e.randomPlay, plexLibrary: e.plexLibrary, localMedia: e.localMedia, favorites: e.favorites, favoritesTransfer: e.favoritesTransfer, handoff: e.handoff, offlineProfile: e.offlineProfile}
		s.resultBytes += len(b)
		s.bytes -= len(e.data)
		copy(d.queue[i:], d.queue[i+1:])
		d.queue[len(d.queue)-1] = entry{}
		d.queue = d.queue[:len(d.queue)-1]
		d.lastSeen = now
		s.mu.Unlock()
		reply(w, 200, map[string]string{"status": "ok"})
		return
	}
	s.mu.Unlock()
	failure(w, 404, "request expired or not found")
}
