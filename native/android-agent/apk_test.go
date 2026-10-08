package main

import (
	"archive/zip"
	"bytes"
	"encoding/binary"
	"os"
	"testing"
	"unicode/utf16"
)

func testManifest(pkg string, utf8 bool) []byte {
	put16 := func(b []byte, off, n int) { binary.LittleEndian.PutUint16(b[off:], uint16(n)) }
	put32 := func(b []byte, off, n int) { binary.LittleEndian.PutUint32(b[off:], uint32(n)) }
	values := []string{"manifest", "package", pkg}
	pool := make([]byte, 28+len(values)*4)
	put16(pool, 0, 1)
	put16(pool, 2, 28)
	put32(pool, 8, len(values))
	put32(pool, 20, len(pool))
	if utf8 {
		put32(pool, 16, 256)
	}
	start := len(pool)
	for i, s := range values {
		put32(pool, 28+i*4, len(pool)-start)
		if utf8 {
			pool = append(pool, byte(len(s)), byte(len(s)))
			pool = append(pool, []byte(s)...)
			pool = append(pool, 0)
		} else {
			units := utf16.Encode([]rune(s))
			pool = append(pool, byte(len(units)), 0)
			for _, u := range units {
				pool = append(pool, byte(u), byte(u>>8))
			}
			pool = append(pool, 0, 0)
		}
	}
	put32(pool, 4, len(pool))
	tag := make([]byte, 56)
	put16(tag, 0, 0x102)
	put16(tag, 2, 16)
	put32(tag, 4, len(tag))
	put32(tag, 16, -1)
	put32(tag, 20, 0)
	put16(tag, 24, 20)
	put16(tag, 26, 20)
	put16(tag, 28, 1)
	put32(tag, 36, -1)
	put32(tag, 40, 1)
	put32(tag, 44, 2)
	put16(tag, 48, 8)
	tag[51] = 3
	put32(tag, 52, 2)
	b := make([]byte, 8)
	put16(b, 0, 3)
	put16(b, 2, 8)
	b = append(b, pool...)
	b = append(b, tag...)
	put32(b, 4, len(b))
	return b
}
func TestAPKPackageIdentity(t *testing.T) {
	for _, utf8 := range []bool{true, false} {
		manifest := testManifest(packageName, utf8)
		got, e := binaryPackage(manifest)
		if e != nil || got != packageName {
			t.Fatalf("manifest parse failed: %v", e)
		}
		var b bytes.Buffer
		z := zip.NewWriter(&b)
		f, _ := z.Create("AndroidManifest.xml")
		f.Write(manifest)
		z.Close()
		got, e = apkPackage(b.Bytes())
		if e != nil || got != packageName {
			t.Fatal("APK identity failed")
		}
		for n := 0; n < len(manifest); n++ {
			if _, e := binaryPackage(manifest[:n]); e == nil {
				t.Fatal("truncated manifest accepted")
			}
		}
	}
	if path := os.Getenv("OTT_TEST_APK"); path != "" {
		b, e := os.ReadFile(path)
		if e != nil {
			t.Fatal(e)
		}
		got, e := apkPackage(b)
		if e != nil || got != packageName {
			t.Fatalf("real wrapper identity: %q %v", got, e)
		}
	}
}
func FuzzBinaryManifest(f *testing.F) {
	f.Add(testManifest(packageName, true))
	f.Add(testManifest(packageName, false))
	f.Add([]byte("invalid"))
	f.Fuzz(func(t *testing.T, b []byte) {
		if len(b) < 2*1024*1024 {
			_, _ = binaryPackage(b)
		}
	})
}
