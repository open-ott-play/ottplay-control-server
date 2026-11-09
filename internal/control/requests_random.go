package control

import "encoding/json"

// The random selector is an explicit union arm. Existing {catalog,id} clients
// and replies retain their original contract; selection stays on the player,
// which owns channel classification, source receipts and parental admission.
type randomPlayRequest struct {
	From, To      int64
	ReceiptScoped bool
}

func randomChannelNumber(raw json.RawMessage) (int64, bool) {
	var value *int64
	if json.Unmarshal(raw, &value) != nil || value == nil || *value < 1 || *value > 9007199254740991 {
		return 0, false
	}
	return *value, true
}

func parseRandomPlayRequest(params map[string]json.RawMessage) *randomPlayRequest {
	if len(params) != 2 {
		return nil
	}
	if catalog, ok := categoryText(params["catalog"], 128); !ok || !categoryCatalogPattern.MatchString(catalog) {
		return nil
	}
	if raw, exists := params["receipt_random"]; exists {
		selector, ok := inspectObject(raw, "version", "scope")
		if !ok || !profileInteger(selector["version"], 2, 2) || !inspectEnum(selector["scope"], "receipt") {
			return nil
		}
		return &randomPlayRequest{ReceiptScoped: true}
	}
	selector, err := decodeObject(params["random"])
	if err != nil {
		return nil
	}
	v := &randomPlayRequest{}
	for key, raw := range selector {
		switch key {
		case "search":
			if _, ok := categoryText(raw, 1024); !ok {
				return nil
			}
		case "mode":
			if !inspectEnum(raw, "adult") {
				return nil
			}
		case "range":
			var bounds []json.RawMessage
			if json.Unmarshal(raw, &bounds) != nil || len(bounds) != 2 {
				return nil
			}
			var fromOK, toOK bool
			v.From, fromOK = randomChannelNumber(bounds[0])
			v.To, toOK = randomChannelNumber(bounds[1])
			if !fromOK || !toOK || v.From > v.To {
				return nil
			}
		default:
			return nil
		}
	}
	return v
}

func validRandomPlayResult(raw json.RawMessage, status string, expected *randomPlayRequest) bool {
	if expected == nil || len(raw) > 65536 {
		return false
	}
	if status != "ok" {
		if expected.ReceiptScoped {
			m, ok := inspectObject(raw, "error")
			_, valid := categoryText(m["error"], 1024)
			return ok && valid && (status == "rejected" || status == "unsupported")
		}
		// Player errors retain the existing play_catalog error envelope.
		return status == "rejected" || status == "unsupported"
	}
	m, ok := inspectObject(raw, "dispatched", "channel")
	if !ok || string(m["dispatched"]) != "true" {
		return false
	}
	channel, ok := inspectObject(m["channel"], "id", "name", "number")
	if !ok {
		return false
	}
	number, ok := randomChannelNumber(channel["number"])
	if !ok || (expected.ReceiptScoped && number > maxCategoryItems) {
		return false
	}
	if _, ok = categoryText(channel["id"], 2048); !ok {
		return false
	}
	nameLimit := 16384
	if expected.ReceiptScoped {
		nameLimit = 512
	}
	if _, ok = categoryText(channel["name"], nameLimit); !ok {
		return false
	}
	return expected.From == 0 || (expected.From <= number && number <= expected.To)
}
