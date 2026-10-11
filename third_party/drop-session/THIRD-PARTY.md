# License and source provenance

Original session, storage and API source is [MIT](LICENSE), contributor `sls_0x`.
There are no npm dependencies, copied libsignal/SPQR files or primitive implementations.
The package stays private pending review.

The caller supplies a Node runtime. The tested runtime is Node 26.1.0 with OpenSSL
3.5.6. Node core is MIT, with bundled components retaining their own notices; review the
complete [Node runtime license](https://github.com/nodejs/node/blob/v26.1.0/LICENSE)
when distributing a runtime. Native crypto uses OpenSSL (Apache-2.0 in the tested
OpenSSL 3 line); native SQLite is [public domain](https://sqlite.org/copyright.html).
This package does not redistribute either executable. Do not describe the entire
runtime distribution as solely MIT.

Test DATA comes from NIST ACVP-Server commit
`975de31eb83d87039ec88934fdc47d8c312b892d`, selected on 2026-10-11. Source paths, group/
case ids and complete downloaded-file SHA-256 digests are in `test/fixtures/nist-acvp.json`.
The full NIST notice is preserved in [NIST-NOTICE.txt](test/fixtures/NIST-NOTICE.txt).
No NIST implementation code was imported. Selection retains one seed-to-public-key
case each for ML-KEM-768 and ML-DSA-65, plus all ten ML-KEM-768 decapsulation cases.
These are not full key-generation, signature-validation or FIPS certification results.

Algorithm references:

- [FIPS 203 ML-KEM](https://csrc.nist.gov/pubs/fips/203/final) and
  [FIPS 204 ML-DSA](https://csrc.nist.gov/pubs/fips/204/final): maintained native primitives;
  monitor the standards' published errata.
- [RFC 7748](https://www.rfc-editor.org/rfc/rfc7748.txt), section 6.1: X25519 known answer.
- [RFC 5869](https://www.rfc-editor.org/rfc/rfc5869.txt), appendix A.1: HKDF known answer.
- [NIST GCM validation](https://csrc.nist.gov/projects/cryptographic-algorithm-validation-program/cavp-testing-block-cipher-modes): AES-GCM standard/testing references.
- [PQXDH](https://signal.org/docs/specifications/pqxdh/) and
  [Double Ratchet](https://signal.org/docs/specifications/doubleratchet/): protocol design
  references. All deviations and the absence of their security-proof transfer are
  described in [PROTOCOL.md](PROTOCOL.md).
- [Native Node crypto API](https://nodejs.org/api/crypto.html): ML-KEM support begins
  at Node 24.7; ML-DSA at 24.6. No fallback algorithm is selected if unsupported.
