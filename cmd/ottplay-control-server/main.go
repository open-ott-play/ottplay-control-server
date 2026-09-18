package main

import (
	"context"
	"crypto/tls"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/open-ott-play/ottplay-control-server/internal/config"
	"github.com/open-ott-play/ottplay-control-server/internal/control"
)

var version = "dev"
var commit = "unknown"
var date = "unknown"

func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr)) }

func run(args []string, out, errOut io.Writer) int {
	if len(args) == 0 {
		fmt.Fprintln(errOut, "Usage: ottplay-control-server <serve|init|validate|version|healthcheck> [options]")
		return 2
	}
	f := flag.NewFlagSet(args[0], flag.ContinueOnError)
	// Flag errors can contain supplied values, including accidental credentials.
	f.SetOutput(io.Discard)
	configPath := ""
	if args[0] == "serve" || args[0] == "init" || args[0] == "validate" {
		f.StringVar(&configPath, "config", "", "Configuration JSON path")
	}
	deviceID, listen, cert, key, probe := "", "", "", "", ""
	switch args[0] {
	case "init":
		f.StringVar(&deviceID, "device-id", "", "Initial device ID")
	case "serve":
		f.StringVar(&listen, "listen", "", "Override listen address")
		f.StringVar(&cert, "tls-cert", "", "TLS certificate PEM path")
		f.StringVar(&key, "tls-key", "", "TLS private key PEM path")
	case "healthcheck":
		f.StringVar(&probe, "url", "http://127.0.0.1:8081/healthz", "Full health probe URL")
	case "version", "validate":
	default:
		fmt.Fprintln(errOut, "Unknown command.")
		return 2
	}
	parseErr := f.Parse(args[1:])
	if errors.Is(parseErr, flag.ErrHelp) {
		fmt.Fprintf(out, "Usage: ottplay-control-server %s [options]\n", args[0])
		f.SetOutput(out)
		f.PrintDefaults()
		return 0
	}
	if parseErr != nil || f.NArg() != 0 {
		fmt.Fprintln(errOut, "Invalid command options.")
		return 2
	}
	if (args[0] == "init" || args[0] == "validate" || args[0] == "serve") && configPath == "" {
		fmt.Fprintln(errOut, "--config is required.")
		return 2
	}
	switch args[0] {
	case "version":
		fmt.Fprintf(out, "ottplay-control-server %s (commit %s, built %s)\n", version, commit, date)
		return 0
	case "init":
		if err := config.Init(configPath, deviceID); err != nil {
			fmt.Fprintln(errOut, err)
			return 1
		}
		fmt.Fprintln(out, "Configuration created with private credentials. Keep the file secret.")
		return 0
	case "healthcheck":
		u, err := url.Parse(probe)
		if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Scheme != "http" && u.Scheme != "https") {
			fmt.Fprintln(errOut, "Invalid healthcheck URL.")
			return 2
		}
		client := &http.Client{Timeout: 5 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
		resp, err := client.Get(probe)
		if err != nil {
			fmt.Fprintln(errOut, "Healthcheck failed.")
			return 1
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			fmt.Fprintln(errOut, "Healthcheck failed.")
			return 1
		}
		fmt.Fprintln(out, "healthy")
		return 0
	}
	c, err := config.Load(configPath)
	if err != nil {
		fmt.Fprintln(errOut, err)
		return 1
	}
	if args[0] == "validate" {
		fmt.Fprintln(out, "Configuration is valid.")
		return 0
	}
	if (cert == "") != (key == "") {
		fmt.Fprintln(errOut, "--tls-cert and --tls-key must be supplied together.")
		return 2
	}
	if listen != "" {
		c.Listen = listen
	}
	if err = c.Validate(); err != nil {
		fmt.Fprintln(errOut, err)
		return 1
	}
	h, err := control.New(c)
	if err != nil {
		fmt.Fprintln(errOut, "Cannot initialize server.")
		return 1
	}
	server := &http.Server{Addr: c.Listen, Handler: h, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 8192, TLSConfig: &tls.Config{MinVersion: tls.VersionTLS12}, ErrorLog: log.New(io.Discard, "", 0)}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	done := make(chan error, 1)
	go func() {
		if cert != "" {
			done <- server.ListenAndServeTLS(cert, key)
		} else {
			done <- server.ListenAndServe()
		}
	}()
	select {
	case err = <-done:
		if !errors.Is(err, http.ErrServerClosed) {
			fmt.Fprintln(errOut, "Server failed to listen or serve.")
			return 1
		}
	case <-ctx.Done():
		shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err = server.Shutdown(shutdown); err != nil {
			_ = server.Close()
			fmt.Fprintln(errOut, "Server shutdown exceeded its deadline.")
			return 1
		}
		<-done
	}
	return 0
}
