// sign-update is an offline operator utility. Its private key never goes to Android.
package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
)

func fail() {
	fmt.Fprintln(os.Stderr, "Use: sign-update generate PRIVATE_KEY | sign-update sign PRIVATE_KEY MANIFEST_JSON OUTPUT_JSON")
	os.Exit(1)
}
func main() {
	if len(os.Args) == 3 && os.Args[1] == "generate" {
		pub, key, e := ed25519.GenerateKey(rand.Reader)
		if e != nil {
			fail()
		}
		f, e := os.OpenFile(os.Args[2], os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if e != nil {
			fail()
		}
		_, e = f.Write(key)
		ce := f.Close()
		if e != nil || ce != nil {
			fail()
		}
		fmt.Println(base64.StdEncoding.EncodeToString(pub))
		return
	}
	if len(os.Args) != 5 || os.Args[1] != "sign" {
		fail()
	}
	st, e := os.Stat(os.Args[2])
	if e != nil || st.Mode().Perm() != 0600 {
		fail()
	}
	key, e := os.ReadFile(os.Args[2])
	if e != nil || len(key) != ed25519.PrivateKeySize {
		fail()
	}
	payload, e := os.ReadFile(os.Args[3])
	if e != nil || len(payload) > 12000 || !json.Valid(payload) {
		fail()
	}
	raw, e := json.Marshal(map[string]string{"payload": base64.StdEncoding.EncodeToString(payload), "signature": base64.StdEncoding.EncodeToString(ed25519.Sign(key, payload))})
	if e != nil {
		fail()
	}
	f, e := os.OpenFile(os.Args[4], os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if e != nil {
		fail()
	}
	_, e = f.Write(raw)
	ce := f.Close()
	if e != nil || ce != nil {
		fail()
	}
	sum := sha256.Sum256(raw)
	fmt.Println(hex.EncodeToString(sum[:]))
}
