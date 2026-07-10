### Title
Backup-CLI `secrets.json` Written Without Restrictive File Permissions, Exposing P2P Private Key and Keyshare Decryption Key in Plaintext - (File: crates/backup-cli/src/adapters/secrets_storage.rs)

### Summary
The `backup-cli` component writes its `secrets.json` file — which contains the Ed25519 P2P private key, the NEAR signer key, and the AES-128 keyshare decryption key — using default OS umask permissions (typically `0o644`, world-readable). The MPC node itself applies explicit `0o600` (owner-only) permissions when writing its own `secrets.json`, but the backup-cli's storage adapter omits this protection entirely. Any local user on the backup service host can read the file and obtain the material needed to impersonate the backup service or decrypt locally stored keyshares.

### Finding Description

The `backup-cli` crate defines `PersistentSecrets` in `crates/backup-cli/src/types.rs`:

```rust
pub struct PersistentSecrets {
    pub p2p_private_key: SigningKey,       // mTLS auth key to MPC nodes
    pub near_signer_key: SigningKey,       // NEAR transaction signing key
    pub local_storage_aes_key: [u8; 16],  // AES-128 key for local keyshare encryption
}
``` [1](#0-0) 

These secrets are serialized as JSON and written to disk by `JsonSecretsStorage::open_write()`:

```rust
let destination = File::options()
    .create(true)
    .truncate(true)
    .write(true)
    .open(file_path)   // ← no mode() call; inherits process umask
    .await
    .map_err(Error::OpenFile)?;
``` [2](#0-1) 

No `set_permissions` or `mode(0o600)` call is made anywhere in the backup-cli crate. A `grep` for `set_permissions`, `mode(0o600)`, or `chmod` in `crates/backup-cli/**` returns zero results.

By contrast, the MPC node's own `secrets.json` is written through `write_secret_file()`, which explicitly enforces owner-only permissions:

```rust
fn write_secret_file(path: &Path, data: &[u8]) -> anyhow::Result<()> {
    let mut file = fs::OpenOptions::new()
        .write(true).create(true).truncate(true)
        .mode(0o600)          // ← set at creation
        .open(path)...;
    ...
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))...;
}
``` [3](#0-2) 

The node's `gen_secrets_and_write_to_disk` calls `write_secret_file` for its own `secrets.json`: [4](#0-3) 

The backup-cli's `generate_secrets` calls `store_secrets` which calls `open_write` — the unprotected path: [5](#0-4) 

### Impact Explanation

An attacker with local user access (non-root) on the backup service host can read `secrets.json` and obtain:

1. **`p2p_private_key`** — used to establish mTLS connections to MPC nodes. The MPC node authenticates the backup service by verifying the TLS peer certificate against the public key registered on-chain. Since the attacker holds the actual private key, they authenticate successfully and can call `GET /get_keyshares`, receiving the node's encrypted keyshare bundle. The node cannot distinguish the attacker from the legitimate backup service.

2. **`local_storage_aes_key`** — used by `KeyshareStorageAdapter` (backed by `LocalPermanentKeyStorageBackend`) to AES-GCM decrypt the keyshare files stored on the backup service host. An attacker who also has filesystem access to the backup home directory can decrypt the stored keyshares directly without any network interaction. [6](#0-5) 

Either path yields one MPC participant's secret key share in plaintext. This is a key-share disclosure below the signing threshold, but it is a concrete, unauthorized extraction of secret MPC material — the exact class of asset the TEE and encryption layers are designed to protect.

### Likelihood Explanation

- The backup-cli is explicitly designed to run **outside** the TEE on an operator-controlled machine, often a shared Linux host or VM.
- On Linux, the default umask is `0022`, producing `0o644` permissions — world-readable. Any local user (e.g., a co-located service, a compromised CI agent, or another operator on a shared host) can `cat secrets.json`.
- The operator guide instructs operators to run `backup-cli generate-keys` as a first step, immediately creating the exposed file.
- The `local_storage_aes_key` and the encrypted keyshare files reside in the same `$BACKUP_HOME_DIR`, so both halves of the decryption puzzle are co-located. [7](#0-6) 

### Recommendation

Apply `mode(0o600)` at file creation and explicitly call `set_permissions` after writing, mirroring the pattern already used in `crates/node/src/config.rs`:

```rust
// In JsonSecretsStorage::open_write():
use std::os::unix::fs::OpenOptionsExt;
let destination = std::fs::OpenOptions::new()
    .create(true)
    .truncate(true)
    .write(true)
    .mode(0o600)
    .open(&file_path)?;
std::fs::set_permissions(&file_path, std::fs::Permissions::from_mode(0o600))?;
```

Additionally, consider encrypting `secrets.json` at rest using a key derived from a hardware-backed source (e.g., TPM, OS keyring, or the TEE's sealed storage), consistent with the long-term recommendation in the referenced report.

### Proof of Concept

```bash
# On the backup service host as an unprivileged user (e.g., 'attacker')
# Operator has already run: backup-cli --home-dir /opt/backup generate-keys

ls -la /opt/backup/secrets.json
# -rw-r--r-- 1 operator operator 312 Jul 10 12:00 /opt/backup/secrets.json
#  ^^^ world-readable

cat /opt/backup/secrets.json
# {
#   "p2p_private_key": "ed25519:<base58-encoded-64-byte-keypair>",
#   "near_signer_key":  "ed25519:<base58-encoded-64-byte-keypair>",
#   "local_storage_aes_key": [42,17,...,99]
# }

# Attacker now has:
# 1. p2p_private_key → can impersonate backup service over mTLS to MPC node
# 2. local_storage_aes_key → can AES-GCM decrypt /opt/backup/permanent_keys/*
#    to recover the stored keyshare in plaintext
```

### Citations

**File:** crates/backup-cli/src/types.rs (L5-13)
```rust
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct PersistentSecrets {
    /// Ed25519 private key used for encrypting P2P communication with MPC nodes
    pub p2p_private_key: SigningKey,
    /// Ed25519 private key used for signing NEAR transactions
    pub near_signer_key: SigningKey,
    /// AES-128 key for encrypting locally stored keyshares
    pub local_storage_aes_key: [u8; 16],
}
```

**File:** crates/backup-cli/src/adapters/secrets_storage.rs (L55-66)
```rust
    pub async fn open_write(storage_path: impl AsRef<Path>) -> Result<Self, Error> {
        let file_path = storage_path.as_ref().join(SECRETS_FILE_NAME);
        let destination = File::options()
            .create(true)
            .truncate(true)
            .write(true)
            .open(file_path)
            .await
            .map_err(Error::OpenFile)?;

        Ok(Self { destination })
    }
```

**File:** crates/node/src/config.rs (L192-208)
```rust
fn write_secret_file(path: &Path, data: &[u8]) -> anyhow::Result<()> {
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(path)
        .with_context(|| format!("failed to create secret file {}", path.display()))?;
    file.write_all(data)
        .with_context(|| format!("failed to write secret file {}", path.display()))?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o600)).with_context(|| {
        format!(
            "failed to set permissions on secret file {}",
            path.display()
        )
    })?;
    Ok(())
```

**File:** crates/node/src/config.rs (L297-302)
```rust
        let path = secrets_file(home_dir);
        if path.exists() {
            anyhow::bail!("secrets.json already exists. Refusing to overwrite.");
        }
        write_secret_file(&path, &serde_json::to_vec(&secrets)?)?;
        tracing::debug!("p2p and near account key generated in {}", path.display());
```

**File:** crates/backup-cli/src/backup.rs (L115-121)
```rust
pub async fn generate_secrets(secrets_storage: &impl ports::SecretsRepository) {
    let persistent_secrets = PersistentSecrets::generate(&mut OsRng);
    secrets_storage
        .store_secrets(&persistent_secrets)
        .await
        .expect("fail to store private key");
}
```

**File:** crates/backup-cli/src/adapters/keyshare_storage.rs (L17-24)
```rust
impl KeyshareStorageAdapter {
    pub async fn new(home_dir: PathBuf, encryption_key: [u8; 16]) -> anyhow::Result<Self> {
        let backend = LocalPermanentKeyStorageBackend::new(home_dir, encryption_key).await?;
        let storage = PermanentKeyStorage::new(Box::new(backend)).await?;

        Ok(Self { storage })
    }
}
```

**File:** docs/node-migration-guide.md (L61-69)
```markdown
  --home-dir $BACKUP_HOME_DIR \
  generate-keys
```

This creates a `secrets.json` file in your backup home directory containing:
- `p2p_private_key`: Used for mutual TLS authentication with MPC nodes
- `local_storage_aes_key`: Used to encrypt keyshares stored locally

**Important:** Keep the `secrets.json` file secure. Anyone with access to this file can authenticate as your backup service and decrypt any keyshares stored locally.
```
