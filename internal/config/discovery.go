package config

import (
	"errors"
	"net"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

// Discovery is opt-in; neither a resolver nor a domain can be chosen by an HTTP caller.
type Discovery struct {
	Domain        string   `json:"domain,omitempty"`
	Nameserver    string   `json:"nameserver,omitempty"`
	PublicURL     string   `json:"public_url"`
	PublicAliases []string `json:"public_aliases,omitempty"`
}

var dnsLabel = regexp.MustCompile(`^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$`)
var basePath = regexp.MustCompile(`^(?:/[a-zA-Z0-9._~-]+)*/?$`)

func ValidDNSHost(host string) bool {
	host = strings.TrimSuffix(host, ".")
	if host == "" || len(host) > 253 {
		return false
	}
	for _, label := range strings.Split(host, ".") {
		if !dnsLabel.MatchString(label) {
			return false
		}
	}
	return true
}

// HTTPSBase rejects credentials, queries, fragments and ambiguous URL paths.
func HTTPSBase(value string) (string, error) {
	u, err := url.Parse(value)
	if err != nil || len(value) > 2048 || u.Scheme != "https" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || u.Opaque != "" || u.RawPath != "" || strings.ContainsAny(value, "\\\r\n\t #%") {
		return "", errors.New("discovery requires an HTTPS base URL without credentials, query or fragment")
	}
	host := u.Hostname()
	port := u.Port()
	if !ValidDNSHost(host) && net.ParseIP(host) == nil {
		return "", errors.New("discovery URL host is invalid")
	}
	if port != "" {
		n, err := strconv.Atoi(port)
		if err != nil || n < 1 || n > 65535 {
			return "", errors.New("discovery URL port is invalid")
		}
	}
	if strings.HasSuffix(u.Host, ":") || !basePath.MatchString(u.Path) {
		return "", errors.New("discovery URL path is invalid")
	}
	for _, part := range strings.Split(u.Path, "/") {
		if part == "." || part == ".." {
			return "", errors.New("discovery URL path is invalid")
		}
	}
	host = strings.TrimSuffix(strings.ToLower(host), ".")
	u.Host = host
	if strings.Contains(host, ":") {
		u.Host = "[" + host + "]"
	}
	// An explicit default TLS port is the same controller as an omitted port.
	if port != "" && port != "443" {
		u.Host = net.JoinHostPort(host, port)
	}
	u.Path = strings.TrimRight(u.Path, "/")
	return u.String(), nil
}

func (d Discovery) Validate() error {
	if d.Domain != "" && !ValidDNSHost(d.Domain) {
		return errors.New("discovery domain must be a DNS domain")
	}
	if d.Nameserver != "" {
		host, port, err := net.SplitHostPort(d.Nameserver)
		n, portErr := strconv.Atoi(port)
		if err != nil || net.ParseIP(host) == nil || portErr != nil || n < 1 || n > 65535 {
			return errors.New("discovery nameserver must be an IP:port address")
		}
	}
	canonical, err := HTTPSBase(d.PublicURL)
	if err != nil {
		return err
	}
	if len(d.PublicAliases) > 8 {
		return errors.New("discovery permits at most 8 public aliases")
	}
	seen := map[string]bool{canonical: true}
	for _, alias := range d.PublicAliases {
		value, err := HTTPSBase(alias)
		if err != nil || seen[value] {
			return errors.New("discovery public aliases must be unique HTTPS bases")
		}
		seen[value] = true
	}
	return nil
}
