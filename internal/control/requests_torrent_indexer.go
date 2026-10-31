package control

// The native companion owns credentials, discovery and download resolution.
// This controller only validates and relays bounded requests and opaque handles.
import (
	"encoding/json"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
)

const maxTorrentIndexerResultBytes = maxResponseBytes
const torrentIndexerError = "Torrent indexer request failed."

var torrentIndexerSourceID = regexp.MustCompile(`^[a-z][a-z0-9-]{0,63}$`)
var torrentIndexerRef = regexp.MustCompile(`^[0-9a-f]{32}$`)
var torrentIndexerTimestamp = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$`)
var torrentIndexerLanguage = regexp.MustCompile(`^[A-Za-z0-9-]{1,32}$`)
var torrentIndexerSourceURI = regexp.MustCompile(`^ottplay-local-torrent:[a-z][a-z0-9-]{0,63}/[0-9a-f]{32}$`)

var torrentIndexerFields = map[string][]string{
	"list":      {"version", "runtime", "op"},
	"configure": {"version", "runtime", "op", "sourceId", "label", "endpoint", "apiKey"},
	"remove":    {"version", "runtime", "op", "sourceId"},
	"caps":      {"version", "runtime", "op", "sourceId"},
	"search":    {"version", "runtime", "op", "sourceId", "query", "categoryIds", "offset", "limit"},
	"prepare":   {"version", "runtime", "op", "sourceId", "resultRef"},
}

var torrentIndexerReasons = map[string]bool{
	"invalid_request":                 true,
	"runtime_changed":                 true,
	"expired":                         true,
	"unsupported":                     true,
	"policy_restricted":               true,
	"unavailable":                     true,
	"unauthorized":                    true,
	"service_stopped":                 true,
	"torznab_invalid_source":          true,
	"torznab_local_only":              true,
	"torznab_invalid_link":            true,
	"torznab_invalid_query":           true,
	"torznab_invalid_category":        true,
	"torznab_search_unavailable":      true,
	"torznab_source_missing":          true,
	"torznab_source_changed":          true,
	"torznab_reference_missing":       true,
	"torznab_busy":                    true,
	"torznab_cancelled":               true,
	"torznab_timeout":                 true,
	"torznab_network_error":           true,
	"torznab_http_error":              true,
	"torznab_response_limit":          true,
	"torznab_invalid_xml":             true,
	"torznab_xml_limit":               true,
	"torznab_api_error":               true,
	"torznab_invalid_page":            true,
	"torznab_store_unavailable":       true,
	"torznab_store_busy":              true,
	"torznab_store_limit":             true,
	"torznab_source_limit":            true,
	"invalid_source":                  true,
	"invalid_tracker":                 true,
	"file_selection_required":         true,
	"metadata_limit":                  true,
	"invalid_metadata":                true,
	"tracker_limit":                   true,
	"companion_transport_unavailable": true,
	"companion_endpoint_invalid":      true,
	"companion_not_configured":        true,
	"companion_timeout":               true,
	"companion_access_denied":         true,
	"companion_request_failed":        true,
	"companion_unavailable":           true,
	"companion_module_unavailable":    true,
}

// Keep credentials and endpoint out of retained request descriptors.
type torrentIndexerRequest struct {
	Runtime, Op, SourceID string
	Offset, Limit         int
}

func torrentIndexerText(raw json.RawMessage, maximum int, empty bool) bool {
	value, ok := inspectString(raw, maximum)
	if !ok || (!empty && strings.TrimSpace(value) == "") {
		return false
	}
	for _, r := range value {
		if unicode.IsControl(r) {
			return false
		}
	}
	return true
}

func torrentIndexerEndpoint(raw json.RawMessage) bool {
	value, ok := inspectString(raw, 4096)
	if !ok || value == "" || strings.ContainsAny(value, "\\?#") {
		return false
	}
	for _, r := range value {
		if unicode.IsControl(r) || unicode.IsSpace(r) {
			return false
		}
	}
	u, err := url.Parse(value)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.User != nil || u.Hostname() == "" || u.Opaque != "" || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || u.RawFragment != "" {
		return false
	}
	if strings.HasSuffix(u.Host, ":") {
		return false
	}
	if port := u.Port(); port != "" {
		n, err := strconv.Atoi(port)
		if err != nil || n < 1 || n > 65535 {
			return false
		}
	}
	return true
}

func torrentIndexerCategories(raw json.RawMessage, maximum int) bool {
	items, ok := inspectArray(raw, maximum)
	if !ok {
		return false
	}
	seen := map[int64]bool{}
	for _, item := range items {
		if !inspectNumber(item, 1, inspectMaxInteger, true, false) {
			return false
		}
		var id int64
		_ = json.Unmarshal(item, &id)
		if seen[id] {
			return false
		}
		seen[id] = true
	}
	return true
}

func torrentIndexerPublishedAt(raw json.RawMessage) bool {
	if inspectNull(raw) {
		return true
	}
	value, ok := inspectString(raw, 64)
	if !ok || !torrentIndexerTimestamp.MatchString(value) {
		return false
	}
	_, err := time.Parse(time.RFC3339Nano, strings.ToUpper(value))
	return err == nil
}

func parseTorrentIndexerRequest(raw json.RawMessage) *torrentIndexerRequest {
	if len(raw) > MaxBodyBytes || !validFavoritesTransferJSON(raw) {
		return nil
	}
	m, err := decodeObject(raw)
	if err != nil || !profileInteger(m["version"], 1, 1) || !torrentCatalogMatch(m["runtime"], torrentCatalogToken) {
		return nil
	}
	q := &torrentIndexerRequest{Runtime: favoritesGlobalString(m["runtime"]), Op: favoritesGlobalString(m["op"])}
	fields, ok := torrentIndexerFields[q.Op]
	if !ok || !favoritesTransferFields(m, fields...) {
		return nil
	}
	if q.Op != "list" {
		if !torrentCatalogMatch(m["sourceId"], torrentIndexerSourceID) {
			return nil
		}
		q.SourceID = favoritesGlobalString(m["sourceId"])
	}
	switch q.Op {
	case "configure":
		if !torrentIndexerText(m["label"], 128, false) || !torrentIndexerEndpoint(m["endpoint"]) || !torrentIndexerText(m["apiKey"], 2048, true) {
			return nil
		}
	case "search":
		if !torrentIndexerText(m["query"], 512, true) || !torrentIndexerCategories(m["categoryIds"], 16) || !profileInteger(m["offset"], 0, 1000000) || !profileInteger(m["limit"], 1, 100) {
			return nil
		}
		q.Offset, q.Limit = favoritesGlobalInt(m["offset"]), favoritesGlobalInt(m["limit"])
	case "prepare":
		if !torrentCatalogMatch(m["resultRef"], torrentIndexerRef) {
			return nil
		}
	}
	return q
}

func validTorrentIndexerEnvelope(raw json.RawMessage) bool {
	if len(raw) > MaxBodyBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "id", "action", "params", "expires_at")
	id, valid := inspectString(m["id"], 32)
	return ok && valid && commandID.MatchString(id) && inspectEnum(m["action"], "torrent_indexer") && numberValue(m["expires_at"], 1, inspectMaxInteger, false) && parseTorrentIndexerRequest(m["params"]) != nil
}

func validTorrentIndexerResultEnvelope(raw json.RawMessage, id string, q *torrentIndexerRequest) bool {
	if q == nil || !commandID.MatchString(id) || len(raw) > maxResponseBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, ok := inspectObject(raw, "id", "status", "data")
	return ok && torrentCatalogEqual(m["id"], id) && validTorrentIndexerResult(m["data"], favoritesGlobalString(m["status"]), q)
}

func validTorrentIndexerResult(raw json.RawMessage, status string, q *torrentIndexerRequest) bool {
	if q == nil || len(raw) > maxTorrentIndexerResultBytes || !validFavoritesTransferJSON(raw) {
		return false
	}
	m, err := decodeObject(raw)
	if err != nil || !profileInteger(m["version"], 1, 1) || !torrentCatalogEqual(m["runtime"], q.Runtime) || !inspectEnum(m["op"], q.Op) {
		return false
	}
	if status == "rejected" || status == "unsupported" {
		return favoritesTransferFields(m, "version", "runtime", "op", "error", "reason") && inspectEnum(m["error"], torrentIndexerError) && torrentIndexerReasons[favoritesGlobalString(m["reason"])]
	}
	if status != "ok" || !profileInteger(m["schema"], 1, 1) {
		return false
	}
	fields := []string{"version", "runtime", "op", "schema"}
	switch q.Op {
	case "list":
		if !favoritesTransferFields(m, append(fields, "sources")...) {
			return false
		}
		sources, ok := inspectArray(m["sources"], 8)
		if !ok {
			return false
		}
		seen := map[string]bool{}
		for _, rawSource := range sources {
			source, ok := inspectObject(rawSource, "id", "label")
			id := favoritesGlobalString(source["id"])
			if !ok || !torrentIndexerSourceID.MatchString(id) || seen[id] || !torrentIndexerText(source["label"], 128, false) {
				return false
			}
			seen[id] = true
		}
		return true
	case "configure":
		if !favoritesTransferFields(m, append(fields, "source")...) {
			return false
		}
		source, ok := inspectObject(m["source"], "id", "label")
		return ok && torrentCatalogEqual(source["id"], q.SourceID) && torrentIndexerText(source["label"], 128, false)
	case "remove":
		return favoritesTransferFields(m, append(fields, "removed")...) && string(m["removed"]) == "true"
	case "caps":
		if !favoritesTransferFields(m, append(fields, "sourceId", "searchAvailable", "categories", "maxLimit")...) || !torrentCatalogEqual(m["sourceId"], q.SourceID) || !inspectBool(m["searchAvailable"], false) || !profileInteger(m["maxLimit"], 1, 100) {
			return false
		}
		categories, ok := inspectArray(m["categories"], 512)
		if !ok {
			return false
		}
		seen := map[int64]bool{}
		for _, rawCategory := range categories {
			category, ok := inspectObject(rawCategory, "id", "name", "parentId")
			if !ok || !inspectNumber(category["id"], 1, inspectMaxInteger, true, false) || !torrentIndexerText(category["name"], 128, false) || !inspectNumber(category["parentId"], 1, inspectMaxInteger, true, true) {
				return false
			}
			var id int64
			_ = json.Unmarshal(category["id"], &id)
			if seen[id] {
				return false
			}
			seen[id] = true
		}
		return true
	case "search":
		if !favoritesTransferFields(m, append(fields, "sourceId", "items", "nextOffset", "total")...) || !torrentCatalogEqual(m["sourceId"], q.SourceID) || !inspectNumber(m["total"], 0, inspectMaxInteger, true, true) {
			return false
		}
		items, ok := inspectArray(m["items"], q.Limit)
		if !ok || (!inspectNull(m["nextOffset"]) && !profileInteger(m["nextOffset"], q.Offset+1, 1000000)) {
			return false
		}
		seen := map[string]bool{}
		for _, rawItem := range items {
			item, ok := inspectObject(rawItem, "resultRef", "title", "size", "seeders", "publishedAt", "categoryIds", "languages")
			ref := favoritesGlobalString(item["resultRef"])
			if !ok || !torrentIndexerRef.MatchString(ref) || seen[ref] || !torrentIndexerText(item["title"], 1024, false) || !inspectNumber(item["size"], 0, inspectMaxInteger, true, true) || !inspectNumber(item["seeders"], 0, inspectMaxInteger, true, true) || !torrentIndexerPublishedAt(item["publishedAt"]) || !torrentIndexerCategories(item["categoryIds"], 32) {
				return false
			}
			seen[ref] = true
			languages, ok := inspectArray(item["languages"], 16)
			if !ok {
				return false
			}
			for _, language := range languages {
				if !torrentCatalogMatch(language, torrentIndexerLanguage) {
					return false
				}
			}
		}
		return true
	case "prepare":
		return favoritesTransferFields(m, append(fields, "sourceId", "sourceRef")...) && torrentCatalogEqual(m["sourceId"], q.SourceID) && torrentCatalogMatch(m["sourceRef"], torrentIndexerRef)
	}
	return false
}
