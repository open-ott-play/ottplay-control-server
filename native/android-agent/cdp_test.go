package main

import (
	"bufio"
	"io"
	"net"
	"testing"
	"time"
)

func TestSocketCloseWaitsForDebuggerRelease(t *testing.T) {
	client, peer := net.Pipe()
	defer peer.Close()
	_ = peer.SetDeadline(time.Now().Add(2 * time.Second))
	s := &socket{Conn: client, r: bufio.NewReader(client)}
	done := make(chan error, 1)
	go func() { done <- s.Close() }()
	frame := make([]byte, 8)
	if _, err := io.ReadFull(peer, frame); err != nil {
		t.Fatal(err)
	}
	if frame[0] != 0x88 || frame[1] != 0x82 {
		t.Fatalf("invalid client close frame: %v", frame)
	}
	select {
	case <-done:
		t.Fatal("disconnected before debugger release")
	default:
	}
	if _, err := peer.Write([]byte{0x88, 2, 3, 232}); err != nil {
		t.Fatal(err)
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}
