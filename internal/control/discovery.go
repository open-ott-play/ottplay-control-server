package control

import (
	"context"
	"errors"
	"net"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/miekg/dns"
	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

const discoveryService = "_ottplay-ctrl._tcp."
const maxDiscovered = 8

type discoveredServer struct {
	ID      string `json:"id"`
	Domain  string `json:"domain"`
	Address string `json:"address"`
}

type discoveryFlight struct {
	done   chan struct{}
	result []discoveredServer
	err    error
}

type discovery struct {
	publicURL string
	mu        sync.Mutex
	cached    []discoveredServer
	expires   time.Time
	err       error
	flight    *discoveryFlight
	lookup    func(context.Context) ([]discoveredServer, time.Duration, error)
}

func newDiscovery(c config.Discovery) *discovery {
	publicURL, _ := config.HTTPSBase(c.PublicURL)
	return &discovery{publicURL: publicURL, lookup: func(ctx context.Context) ([]discoveredServer, time.Duration, error) { return discoverDNS(ctx, c) }}
}

// Concurrent callers share one bounded lookup. Approval and redemption force a
// fresh lookup, so a cached descriptor cannot silently authorize a new target.
func (d *discovery) resolve(ctx context.Context, refresh bool) ([]discoveredServer, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	d.mu.Lock()
	if !refresh && time.Now().Before(d.expires) {
		result, err := append([]discoveredServer{}, d.cached...), d.err
		d.mu.Unlock()
		return result, err
	}
	flight := d.flight
	if flight == nil {
		flight = &discoveryFlight{done: make(chan struct{})}
		d.flight = flight
		go d.resolveFlight(flight)
	}
	d.mu.Unlock()
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-flight.done:
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		return append([]discoveredServer{}, flight.result...), flight.err
	}
}

func (d *discovery) resolveFlight(flight *discoveryFlight) {
	// A disconnected caller must not cancel another caller's validation. The
	// shared work remains bounded even when every HTTP caller stops waiting.
	bounded, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	result, ttl, err := d.lookup(bounded)
	cancel()
	if err != nil {
		result = nil
		ttl = 2 * time.Second
	}
	if ttl > 30*time.Second {
		ttl = 30 * time.Second
	}
	d.mu.Lock()
	d.cached, d.expires, d.err = append([]discoveredServer{}, result...), time.Now().Add(ttl), err
	flight.result, flight.err = d.cached, err
	d.flight = nil
	close(flight.done)
	d.mu.Unlock()
}

func discoverDNS(ctx context.Context, c config.Discovery) ([]discoveredServer, time.Duration, error) {
	domains, servers := []string{}, []string{}
	if c.Domain != "" {
		domains = append(domains, strings.TrimSuffix(strings.ToLower(c.Domain), "."))
	}
	if c.Nameserver != "" {
		servers = append(servers, c.Nameserver)
	}
	if len(domains) == 0 || len(servers) == 0 {
		system, err := dns.ClientConfigFromFile("/etc/resolv.conf")
		if err != nil {
			return nil, 0, errors.New("system DNS configuration unavailable")
		}
		if len(domains) == 0 {
			for _, domain := range system.Search {
				if config.ValidDNSHost(domain) && len(domains) < 4 {
					domains = append(domains, strings.TrimSuffix(strings.ToLower(domain), "."))
				}
			}
		}
		if len(servers) == 0 {
			for _, server := range system.Servers {
				if net.ParseIP(server) != nil && len(servers) < 3 {
					servers = append(servers, net.JoinHostPort(server, system.Port))
				}
			}
		}
	}
	if len(domains) == 0 || len(servers) == 0 {
		return nil, 0, errors.New("discovery needs a configured domain or system search domain and resolver")
	}
	ttl := uint32(30)
	ask := func(name string, kind uint16) ([]dns.RR, error) {
		var last error
		for _, server := range servers {
			message := new(dns.Msg)
			message.SetQuestion(dns.Fqdn(name), kind)
			message.SetEdns0(1232, false)
			client := &dns.Client{Net: "udp", Timeout: time.Second, UDPSize: 1232}
			response, _, err := client.ExchangeContext(ctx, message, server)
			if err == nil && response.Truncated {
				client.Net = "tcp"
				response, _, err = client.ExchangeContext(ctx, message, server)
			}
			if err != nil {
				last = err
				continue
			}
			if !response.Response || response.Opcode != dns.OpcodeQuery || len(response.Question) != 1 || !strings.EqualFold(response.Question[0].Name, message.Question[0].Name) || response.Question[0].Qtype != kind || response.Question[0].Qclass != dns.ClassINET {
				last = errors.New("DNS response does not match the discovery question")
				continue
			}
			if response.Rcode == dns.RcodeNameError {
				return []dns.RR{}, nil
			}
			if response.Rcode != dns.RcodeSuccess || len(response.Answer) > 64 {
				last = errors.New("DNS response failed or exceeded discovery bounds")
				continue
			}
			records := []dns.RR{}
			for _, record := range response.Answer {
				if strings.EqualFold(record.Header().Name, dns.Fqdn(name)) && record.Header().Rrtype == kind && record.Header().Class == dns.ClassINET {
					records = append(records, record)
					if record.Header().Ttl < ttl {
						ttl = record.Header().Ttl
					}
				}
			}
			return records, nil
		}
		return nil, last
	}
	result := []discoveredServer{}
	seen := map[string]bool{}
	for _, domain := range domains {
		service := discoveryService + domain + "."
		records, err := ask(service, dns.TypePTR)
		if err != nil {
			return nil, 0, err
		}
		if len(records) > maxDiscovered {
			return nil, 0, errors.New("too many discovery instances")
		}
		for _, record := range records {
			instance := strings.ToLower(record.(*dns.PTR).Ptr)
			if seen[instance] {
				continue
			}
			seen[instance] = true
			labels := dns.SplitDomainName(instance)
			if len(instance) > 254 || !dns.IsSubDomain(service, instance) || len(labels) != len(dns.SplitDomainName(service))+1 || !config.ValidDNSHost(labels[0]) {
				continue
			}
			services, err := ask(instance, dns.TypeSRV)
			if err != nil {
				return nil, 0, err
			}
			if len(services) != 1 {
				continue
			}
			texts, err := ask(instance, dns.TypeTXT)
			if err != nil {
				return nil, 0, err
			}
			if len(texts) != 1 {
				continue
			}
			srv := services[0].(*dns.SRV)
			target := strings.TrimSuffix(strings.ToLower(srv.Target), ".")
			if !config.ValidDNSHost(target) || srv.Port == 0 {
				continue
			}
			fields := map[string]string{}
			valid := true
			size := 0
			for _, text := range texts[0].(*dns.TXT).Txt {
				size += len(text)
				key, value, ok := strings.Cut(text, "=")
				if !ok || size > 1024 || (key != "txtvers" && key != "scheme" && key != "path") {
					valid = false
					break
				}
				if _, exists := fields[key]; exists {
					valid = false
					break
				}
				fields[key] = value
			}
			if !valid || len(fields) != 3 || fields["txtvers"] != "1" || fields["scheme"] != "https" || !strings.HasPrefix(fields["path"], "/") {
				continue
			}
			authority := target
			if srv.Port != 443 {
				authority = net.JoinHostPort(target, strconv.Itoa(int(srv.Port)))
			}
			address, err := config.HTTPSBase("https://" + authority + fields["path"])
			if err != nil {
				continue
			}
			result = append(result, discoveredServer{ID: instance, Domain: domain, Address: address})
			if len(result) > maxDiscovered {
				return nil, 0, errors.New("too many discovery servers")
			}
		}
	}
	sort.Slice(result, func(i, j int) bool { return result[i].ID < result[j].ID })
	return result, time.Duration(ttl) * time.Second, nil
}
