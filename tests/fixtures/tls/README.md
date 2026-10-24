# Local TLS test fixture

These public test-only credentials are used solely by the loopback HTTPS server
in `tests/test_diagnostics_cli.py`. Never use them for a deployed server or add
this certificate to an operating system trust store. The test trusts it only in
its own client context.

The self-signed P-256 certificate has `DNS:localhost` as its only subject
alternative name and is valid from 2020-01-01 through 2120-01-01. A connection to
`127.0.0.1` must therefore fail hostname verification even with this certificate
explicitly trusted. The long validity avoids time-dependent test failures.

Keeping the fixture in the test directory removes external OpenSSL processes
and random key-generation delays from every test run. The production package
does not use these files. The key was generated solely for this fixture and is
intentionally public.
