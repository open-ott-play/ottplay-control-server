package main

import (
	"archive/zip"
	"bytes"
	"encoding/binary"
	"errors"
	"io"
	"unicode/utf16"
)

// Read the package from the APK's binary manifest before asking PackageManager
// to install it. A signed envelope naming our package is not evidence that the
// enclosed APK actually contains that package.
func apkPackage(raw []byte) (string, error) {
	z, e := zip.NewReader(bytes.NewReader(raw), int64(len(raw)))
	if e != nil {
		return "", e
	}
	var manifest []byte
	for _, f := range z.File {
		if f.Name != "AndroidManifest.xml" {
			continue
		}
		if manifest != nil || f.UncompressedSize64 > 2*1024*1024 {
			return "", errors.New("invalid APK manifest")
		}
		r, e := f.Open()
		if e != nil {
			return "", e
		}
		manifest, e = io.ReadAll(io.LimitReader(r, 2*1024*1024+1))
		r.Close()
		if e != nil || len(manifest) > 2*1024*1024 {
			return "", errors.New("invalid APK manifest")
		}
	}
	return binaryPackage(manifest)
}
func binaryPackage(b []byte) (string, error) {
	bad := errors.New("unsupported binary manifest")
	u16 := func(b []byte) int { return int(binary.LittleEndian.Uint16(b)) }
	u32 := func(b []byte) int { return int(binary.LittleEndian.Uint32(b)) }
	if len(b) < 8 || u16(b) != 3 || u16(b[2:]) != 8 || u32(b[4:]) != len(b) {
		return "", bad
	}
	var pool []string
	for offset := 8; offset < len(b); {
		if len(b)-offset < 8 {
			return "", bad
		}
		c := b[offset:]
		kind, header, size := u16(c), u16(c[2:]), u32(c[4:])
		if header < 8 || size < header || size > len(c) {
			return "", bad
		}
		c = c[:size]
		offset += size
		if kind == 1 {
			if pool != nil || header < 28 {
				return "", bad
			}
			count, flags, start := u32(c[8:]), u32(c[16:]), u32(c[20:])
			if count > 65536 || count < 1 || count > (size-header)/4 || start < header+count*4 || start >= size {
				return "", bad
			}
			pool = make([]string, count)
			for i := 0; i < count; i++ {
				index := u32(c[header+i*4:])
				if index < 0 || index >= size-start {
					return "", bad
				}
				s, e := poolString(c[start+index:], flags&256 != 0)
				if e != nil {
					return "", e
				}
				pool[i] = s
			}
		}
		if kind == 0x102 {
			if header < 16 || size-header < 20 || pool == nil {
				return "", bad
			}
			ext := c[header:]
			name, attrStart, attrSize, count := u32(ext[4:]), u16(ext[8:]), u16(ext[10:]), u16(ext[12:])
			if name < 0 || name >= len(pool) || pool[name] != "manifest" {
				return "", bad
			}
			if attrStart < 20 || attrSize < 20 || attrStart > len(ext) || count > (len(ext)-attrStart)/attrSize {
				return "", bad
			}
			found := ""
			for i := 0; i < count; i++ {
				a := ext[attrStart+i*attrSize:]
				name := u32(a[4:])
				if name < 0 || name >= len(pool) {
					return "", bad
				}
				if pool[name] != "package" {
					continue
				}
				if binary.LittleEndian.Uint32(a) != 0xffffffff || found != "" {
					return "", bad
				}
				value := u32(a[8:])
				if binary.LittleEndian.Uint32(a[8:]) == 0xffffffff {
					if a[15] != 3 {
						return "", bad
					}
					value = u32(a[16:])
				}
				if value < 0 || value >= len(pool) {
					return "", bad
				}
				found = pool[value]
			}
			if found == "" {
				return "", bad
			}
			return found, nil
		}
	}
	return "", bad
}
func poolString(b []byte, utf8 bool) (string, error) {
	bad := errors.New("invalid manifest string")
	read8 := func() (int, bool) {
		if len(b) < 1 {
			return 0, false
		}
		n := int(b[0])
		b = b[1:]
		if n&128 != 0 {
			if len(b) < 1 {
				return 0, false
			}
			n = (n&127)<<8 | int(b[0])
			b = b[1:]
		}
		return n, true
	}
	if utf8 {
		if _, ok := read8(); !ok {
			return "", bad
		}
		n, ok := read8()
		if !ok || n >= len(b) || b[n] != 0 {
			return "", bad
		}
		return string(b[:n]), nil
	}
	if len(b) < 2 {
		return "", bad
	}
	n := uint32(binary.LittleEndian.Uint16(b))
	b = b[2:]
	if n&0x8000 != 0 {
		if len(b) < 2 {
			return "", bad
		}
		n = (n&0x7fff)<<16 | uint32(binary.LittleEndian.Uint16(b))
		b = b[2:]
	}
	if uint64(n)*2+2 > uint64(len(b)) || binary.LittleEndian.Uint16(b[int(n)*2:]) != 0 {
		return "", bad
	}
	units := make([]uint16, int(n))
	for i := range units {
		units[i] = binary.LittleEndian.Uint16(b[i*2:])
	}
	return string(utf16.Decode(units)), nil
}
