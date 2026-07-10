### Title
Plaintext Key Share Material Exposed via CLI Positional Arguments and Unredacted `Debug` Derive — (File: `crates/node/src/cli.rs`)

### Summary
The `ImportKeyshareCmd` CLI subcommand accepts the full plaintext keyshare JSON (containing the private key share scalar) and the AES-128 disk encryption key as positional command-line arguments. Both secrets are simultaneously visible in OS process listings (`/proc/<pid>/cmdline`), shell history files, and system audit logs for the lifetime of the process. Additionally, both `ImportKeyshareCmd` and the parent `CliCommand` enum derive `Debug` without redaction, meaning any framework-level error logging that formats the parsed CLI struct will leak the raw key material. This is the direct structural analog of HAL-26: secret key material held in plaintext in an observable, unprotected location.

### Finding Description

`ImportKeyshareCmd` is defined as:

```rust
// crates/node/src/cli.rs, lines 209-224
#[derive(Args, Debug)]
pub struct ImportKeyshareCmd {
    #[arg(long, env("MPC_HOME_DIR"))]
    pub home_dir: String,

    #[arg(help = "JSON string with the keyshare in format: ...")]
    pub keyshare_json: String,          // ← raw private key share scalar

    #[arg(help = "Hex-encoded 16 byte AES key for local storage encryption")]
    pub local_encryption_key_hex: String, // ← disk encryption key
}
``` [1](#0-0) 

Both `keyshare_json` and `local_encryption_key_hex` are positional arguments with no `--` flag, so the operator invokes the command as:

```
mpc-node import-keyshare --home-dir /data '{"epoch":1,"private_share":"<scalar>","public_key":"<point>"}' 0123456789ABCDEF...
```

The entire command line, including both secrets, is immediately visible in:
- `/proc/<pid>/cmdline` — readable by any process on the same host with default Linux permissions
- Shell history files (`.bash_history`, `.zsh_history`, etc.)
- System audit logs (`auditd`, `syslog`, journald process-exec events)

The parent enum also derives `Debug` without redaction:

```rust
// crates/node/src/cli.rs, lines 33-48
#[derive(Subcommand, Debug)]
pub enum CliCommand {
    ...
    ImportKeyshare(ImportKeyshareCmd),
    ...
}
``` [2](#0-1) 

If any framework-level error handler or tracing subscriber formats the parsed `Cli` struct with `{:?}`, both the raw keyshare JSON and the AES key are emitted to logs in plaintext.

The `Keyshare` and `KeyshareData` structs themselves also derive `Debug` without redaction:

```rust
// crates/node/src/keyshare.rs, lines 21-33
#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
pub enum KeyshareData { ... }

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Keyshare {
    pub key_id: KeyEventId,
    pub data: KeyshareData,   // ← contains private_share scalar
}
``` [3](#0-2) 

This contrasts with the deliberate redaction already applied to `MpcPeerMessage` computation payloads, which the codebase explicitly tests:

```rust
// crates/node/src/primitives.rs, lines 410-435
fn mpc_peer_message_debug__should_redact_computation_payload() { ... }
``` [4](#0-3) 

The same protection is absent from `Keyshare`, `KeyshareData`, and `ImportKeyshareCmd`.

Additionally, `ExportKeyshareCmd::run` prints the full keyshare (including `private_share`) to stdout in plaintext:

```rust
// crates/node/src/cli.rs, lines 371-374
let json = serde_json::to_string_pretty(&keyshare)?;
println!("{}", json);
``` [5](#0-4) 

### Impact Explanation

An attacker who controls the host OS — which is the **explicit baseline assumption** in the TEE threat model ("Host OS | Red | UNTRUSTED — full root access assumed") — can passively read `/proc/<pid>/cmdline` during or after the `import-keyshare` invocation and recover:

1. The raw private key share scalar (from `keyshare_json`)
2. The AES-128 disk encryption key (from `local_encryption_key_hex`)

With both values the attacker can also decrypt any future keyshare files written to the node's home directory. This constitutes unauthorized access to MPC key share material. A single exposed share reduces the effective security of the threshold scheme; if the same host-level attacker can observe the import operation across multiple nodes (e.g., during a coordinated migration), they can accumulate enough shares to reconstruct the full private key and issue unauthorized threshold signatures.

**Impact: Critical** — unauthorized access to MPC key shares and the encryption key protecting them at rest, directly enabling secret recovery if repeated across threshold-many nodes.

### Likelihood Explanation

The TEE threat model explicitly treats the host OS as untrusted with full root access. The `import-keyshare` command is a documented, operator-facing migration step run on the host machine. Reading `/proc/<pid>/cmdline` requires no special exploit — it is a standard Linux facility available to any process running as the same user or as root. Shell history persistence is automatic and requires no active interception. The exposure window is not transient: shell history and audit logs persist indefinitely.

**Likelihood: Medium** — requires the host OS to be in the adversarial position assumed by the TEE threat model, and the operator to perform a migration using the CLI tool.

### Recommendation

1. **Remove secrets from positional CLI arguments.** Read `keyshare_json` from a file path argument or from stdin (e.g., `--keyshare-file <path>` or piped input). Read `local_encryption_key_hex` from an environment variable (already supported for `MPC_HOME_DIR`) or a secrets file, never as a positional argument.

2. **Implement `Debug` redaction for `Keyshare`, `KeyshareData`, and `ImportKeyshareCmd`.** Replace the `#[derive(Debug)]` with a manual `impl Debug` that emits only non-sensitive metadata (e.g., `key_id`, epoch, domain), consistent with the existing redaction pattern on `MpcPeerMessage`.

3. **Avoid printing keyshare material to stdout.** `ExportKeyshareCmd` should write to a file with restricted permissions rather than stdout, or at minimum warn that the output contains secret material and should not be logged.

4. **Zeroize sensitive buffers.** The `keyshare_json: String` and `local_encryption_key_hex: String` fields in `ImportKeyshareCmd` should use a zeroizing string type (e.g., `zeroize::Zeroizing<String>`) so the memory is cleared when the struct is dropped.

### Proof of Concept

```bash
# Operator runs the documented migration command on the host machine:
mpc-node import-keyshare \
  --home-dir /data \
  '{"epoch":1,"private_share":"<scalar_hex>","public_key":"<point_hex>"}' \
  0123456789ABCDEF0123456789ABCDEF

# Attacker (host-level process) reads the arguments before the process exits:
cat /proc/$(pgrep mpc-node)/cmdline | tr '\0' '\n'
# Output includes:
# import-keyshare
# --home-dir
# /data
# {"epoch":1,"private_share":"<scalar_hex>","public_key":"<point_hex>"}
# 0123456789ABCDEF0123456789ABCDEF   ← AES disk encryption key also exposed
```

The attacker now holds the raw private key share scalar and the key needed to decrypt the keyshare file on disk, satisfying both exposure vectors described in HAL-26 (in-memory plaintext and clipboard/process-observable plaintext).

### Citations

**File:** crates/node/src/cli.rs (L33-48)
```rust
#[derive(Subcommand, Debug)]
pub enum CliCommand {
    /// Starts the MPC node using a single TOML configuration file instead of
    /// environment variables and CLI flags.
    StartWithConfigFile {
        /// Path to a TOML configuration file containing all settings needed to
        /// start the MPC node.
        config_path: PathBuf,
    },
    Start(StartCmd),
    /// Generates/downloads required files for Near node to run
    Init(InitConfigArgs),
    /// Imports a keyshare from JSON and stores it in the local encrypted storage
    ImportKeyshare(ImportKeyshareCmd),
    /// Exports a keyshare from local encrypted storage and prints it to the console
    ExportKeyshare(ExportKeyshareCmd),
```

**File:** crates/node/src/cli.rs (L209-224)
```rust
#[derive(Args, Debug)]
pub struct ImportKeyshareCmd {
    /// Path to home directory
    #[arg(long, env("MPC_HOME_DIR"))]
    pub home_dir: String,

    /// JSON string containing the keyshare to import
    #[arg(
        help = "JSON string with the keyshare in format: {\"epoch\":1,\"private_share\":\"...\",\"public_key\":\"...\"}"
    )]
    pub keyshare_json: String,

    /// Hex-encoded 16 byte AES key for local storage encryption
    #[arg(help = "Hex-encoded 16 byte AES key for local storage encryption")]
    pub local_encryption_key_hex: String,
}
```

**File:** crates/node/src/cli.rs (L371-374)
```rust
            let json = serde_json::to_string_pretty(&keyshare)
                .map_err(|e| anyhow::anyhow!("Failed to serialize keyshare: {}", e))?;

            println!("{}", json);
```

**File:** crates/node/src/keyshare.rs (L21-33)
```rust
#[derive(Debug, Clone, Serialize, Deserialize, Eq, PartialEq)]
pub enum KeyshareData {
    Secp256k1(threshold_signatures::ecdsa::KeygenOutput),
    Ed25519(threshold_signatures::frost::eddsa::KeygenOutput),
    Bls12381(threshold_signatures::confidential_key_derivation::KeygenOutput),
}

/// A single keyshare, corresponding to one epoch, one domain, one attempt.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Keyshare {
    pub key_id: KeyEventId,
    pub data: KeyshareData,
}
```

**File:** crates/node/src/primitives.rs (L410-435)
```rust
    fn mpc_peer_message_debug__should_redact_computation_payload() {
        // given
        let secret_data = b"PRIVATE_KEY_SHARE_MATERIAL".to_vec();
        let message = MpcPeerMessage {
            from: ParticipantId::from_raw(1),
            message: MpcMessage {
                channel_id: ChannelId(UniqueId::new(ParticipantId::from_raw(0), 99, 0)),
                kind: MpcMessageKind::Computation(vec![secret_data]),
            },
        };

        // when
        let debug_output = format!("{:?}", message);

        // then
        assert!(
            !debug_output.contains("PRIVATE_KEY_SHARE"),
            "MpcPeerMessage debug must not leak computation bytes, got: {}",
            debug_output
        );
        assert!(
            debug_output.contains("1 chunks"),
            "Should show chunk metadata, got: {}",
            debug_output
        );
    }
```
