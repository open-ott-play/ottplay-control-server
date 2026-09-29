package control

import (
	"context"
	"errors"
	"net"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/miekg/dns"
	"github.com/open-ott-play/ottplay-control-server/internal/config"
)

func dnsFixture(t *testing.T, records func(dns.Question) []dns.RR) string {
	t.Helper()
	packet, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	server := &dns.Server{PacketConn: packet, Handler: dns.HandlerFunc(func(w dns.ResponseWriter, r *dns.Msg) {
		answer := new(dns.Msg)
		answer.SetReply(r)
		answer.Answer = records(r.Question[0])
		_ = w.WriteMsg(answer)
	})}
	done := make(chan error, 1)
	go func() { done <- server.ActivateAndServe() }()
	t.Cleanup(func() { _ = server.Shutdown(); <-done })
	return packet.LocalAddr().String()
}

func fixtureRecords(q dns.Question) []dns.RR {
	name := "home._ottplay-ctrl._tcp.example.test."
	rr := func(text string) dns.RR {
		r, err := dns.NewRR(text)
		if err != nil {
			panic(err)
		}
		return r
	}
	switch q.Qtype {
	case dns.TypePTR:
		return []dns.RR{rr(q.Name + " 30 IN PTR " + name)}
	case dns.TypeSRV:
		return []dns.RR{rr(q.Name + " 30 IN SRV 0 0 443 controller.example.test.")}
	case dns.TypeTXT:
		return []dns.RR{rr(q.Name + ` 30 IN TXT "txtvers=1" "scheme=https" "path=/ott-control"`)}
	}
	return nil
}

func TestDNSServiceDiscoveryAndValidation(t *testing.T) {
	for _, scenario := range []string{"valid", "wrong_owner", "external_instance", "multiple_srv", "insecure", "credentials", "traversal", "duplicate_txt", "unknown_txt", "invalid_instance"} {
		t.Run(scenario, func(t *testing.T) {
			resolver := dnsFixture(t, func(q dns.Question) []dns.RR {
				rows := fixtureRecords(q)
				switch scenario {
				case "wrong_owner":
					for _, rr := range rows {
						rr.Header().Name = "other.example.test."
					}
				case "external_instance":
					if q.Qtype == dns.TypePTR {
						rows[0].(*dns.PTR).Ptr = "home._ottplay-ctrl._tcp.evil.test."
					}
				case "invalid_instance":
					if q.Qtype == dns.TypePTR {
						rows[0].(*dns.PTR).Ptr = `bad\032name._ottplay-ctrl._tcp.example.test.`
					}
				case "multiple_srv":
					if q.Qtype == dns.TypeSRV {
						rows = append(rows, rows[0])
					}
				case "insecure":
					if q.Qtype == dns.TypeTXT {
						rows[0].(*dns.TXT).Txt[1] = "scheme=http"
					}
				case "credentials":
					if q.Qtype == dns.TypeSRV {
						rows[0].(*dns.SRV).Target = "user@evil.test."
					}
				case "traversal":
					if q.Qtype == dns.TypeTXT {
						rows[0].(*dns.TXT).Txt[2] = "path=/../private"
					}
				case "duplicate_txt":
					if q.Qtype == dns.TypeTXT {
						rows[0].(*dns.TXT).Txt = append(rows[0].(*dns.TXT).Txt, "scheme=https")
					}
				case "unknown_txt":
					if q.Qtype == dns.TypeTXT {
						rows[0].(*dns.TXT).Txt = append(rows[0].(*dns.TXT).Txt, "token=forbidden")
					}
				}
				return rows
			})
			got, ttl, err := discoverDNS(context.Background(), config.Discovery{Domain: "example.test", Nameserver: resolver})
			if err != nil {
				t.Fatal(err)
			}
			if scenario == "valid" {
				if len(got) != 1 || got[0].ID != "home._ottplay-ctrl._tcp.example.test." || got[0].Address != "https://controller.example.test/ott-control" || ttl != 30*time.Second {
					t.Fatalf("incorrect descriptor: %+v %v", got, ttl)
				}
			} else if len(got) != 0 {
				t.Fatalf("unsafe DNS descriptor accepted: %+v", got)
			}
		})
	}
}

func TestDiscoveryCoalescesAndBoundsLookups(t *testing.T) {
	var count atomic.Int32
	started, release := make(chan struct{}), make(chan struct{})
	d := &discovery{lookup: func(ctx context.Context) ([]discoveredServer, time.Duration, error) {
		if count.Add(1) == 1 {
			close(started)
		}
		select {
		case <-release:
		case <-ctx.Done():
			return nil, 0, ctx.Err()
		}
		return []discoveredServer{{ID: "fixture"}}, 30 * time.Second, nil
	}}
	var wait sync.WaitGroup
	for i := 0; i < 20; i++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			_, err := d.resolve(context.Background(), false)
			if err != nil {
				t.Error(err)
			}
		}()
	}
	<-started
	close(release)
	wait.Wait()
	if count.Load() != 1 {
		t.Fatalf("lookup amplification: %d", count.Load())
	}
	_, _ = d.resolve(context.Background(), true)
	if count.Load() != 2 {
		t.Fatal("fresh validation used cached DNS")
	}
	failed := &discovery{lookup: func(ctx context.Context) ([]discoveredServer, time.Duration, error) {
		<-ctx.Done()
		return nil, 0, ctx.Err()
	}}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if _, err := failed.resolve(ctx, false); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("lookup did not honor cancellation: %v", err)
	}
}

func TestDNSInstanceAndAnswerBounds(t *testing.T) {
	resolver := dnsFixture(t, func(q dns.Question) []dns.RR {
		if q.Qtype != dns.TypePTR {
			return fixtureRecords(q)
		}
		records := []dns.RR{}
		for i := 0; i < maxDiscovered+1; i++ {
			records = append(records, &dns.PTR{Hdr: dns.RR_Header{Name: q.Name, Rrtype: dns.TypePTR, Class: dns.ClassINET, Ttl: 30}, Ptr: strings.Repeat("a", i+1) + "._ottplay-ctrl._tcp.example.test."})
		}
		return records
	})
	_, _, err := discoverDNS(context.Background(), config.Discovery{Domain: "example.test", Nameserver: resolver})
	if err == nil {
		t.Fatal("unbounded DNS service list accepted")
	}
}

func TestDNSTruncationUsesTCPAndHonorsTTL(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	packet, err := net.ListenPacket("udp", listener.Addr().String())
	if err != nil {
		listener.Close()
		t.Fatal(err)
	}
	var udpCount, tcpCount atomic.Int32
	handler := dns.HandlerFunc(func(w dns.ResponseWriter, r *dns.Msg) {
		answer := new(dns.Msg)
		answer.SetReply(r)
		if _, udp := w.RemoteAddr().(*net.UDPAddr); udp {
			udpCount.Add(1)
			answer.Truncated = true
		} else {
			tcpCount.Add(1)
			answer.Answer = fixtureRecords(r.Question[0])
			for _, rr := range answer.Answer {
				rr.Header().Ttl = 7
			}
		}
		_ = w.WriteMsg(answer)
	})
	udp, tcp := &dns.Server{PacketConn: packet, Handler: handler}, &dns.Server{Listener: listener, Handler: handler}
	done := make(chan error, 2)
	go func() { done <- udp.ActivateAndServe() }()
	go func() { done <- tcp.ActivateAndServe() }()
	t.Cleanup(func() { _ = udp.Shutdown(); _ = tcp.Shutdown(); <-done; <-done })
	result, ttl, err := discoverDNS(context.Background(), config.Discovery{Domain: "example.test", Nameserver: listener.Addr().String()})
	if err != nil || len(result) != 1 || ttl != 7*time.Second || udpCount.Load() != 3 || tcpCount.Load() != 3 {
		t.Fatalf("TCP fallback or TTL failed: %+v %v %v udp=%d tcp=%d", result, ttl, err, udpCount.Load(), tcpCount.Load())
	}
}

func TestDiscoveryHTTPDoesNotHoldQueueMutexDuringLookup(t *testing.T) {
	s := pairingServer(t)
	started, release := make(chan struct{}), make(chan struct{})
	s.discovery.lookup = func(ctx context.Context) ([]discoveredServer, time.Duration, error) {
		close(started)
		select {
		case <-release:
			return []discoveredServer{homeServer}, 30 * time.Second, nil
		case <-ctx.Done():
			return nil, 0, ctx.Err()
		}
	}
	done := make(chan int, 1)
	go func() { done <- request(s, "GET", "/api/discovery", "", "", nil).Code }()
	<-started
	expect(t, request(s, "GET", "/api/webhook/commands?delivery=ack", firstToken, "", nil), 200)
	close(release)
	if status := <-done; status != 200 {
		t.Fatalf("discovery returned %d", status)
	}
}
