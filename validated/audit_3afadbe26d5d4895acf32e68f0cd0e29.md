### Title
Unauthenticated `AddGasPrice` and `Initialize` Write Operations on `L1GasPriceProvider` Remote Server Allow Arbitrary Gas Price Injection - (File: `crates/apollo_l1_gas_price/src/communication.rs`)

---

### Summary

The `L1GasPriceProvider` exposes mutating operations — `L1GasPriceRequest::AddGasPrice` and `L1GasPriceRequest::Initialize` — through the same unauthenticated `RemoteComponentServer` (HTTP/2) that serves read requests. These write paths are architecturally intended to be called exclusively by the co-located `L1GasPriceScraper`, but the `RemoteComponentServer` performs zero caller verification. Any network-reachable process can reset the provider's ring buffer and inject arbitrary `GasPriceData`, corrupting the locally-trusted L1 gas price reference that the consensus orchestrator uses to validate every `ProposalInit`.

---

### Finding Description

**Root cause — no access control on the shared remote server**

`RemoteComponentServer` in `crates/apollo_infra/src/component_server/remote_component_server.rs` is a generic HTTP/2 server. Its `remote_component_server_handler` deserializes any well-formed request body and forwards it to the local component without inspecting the caller's identity: [1](#0-0) 

The `L1GasPriceProvider`'s `ComponentRequestHandler` dispatches every `L1GasPriceRequest` variant — including the two write variants — without any guard: [2](#0-1) 

The write variants are: [3](#0-2) 

**`Initialize` resets the ring buffer to empty**, after which the consecutive-block-number guard in `add_price_info` no longer applies (there is no "previous" entry), so the attacker can immediately push `GasPriceData` starting at any block number: [4](#0-3) 

**Production deployment binds to `0.0.0.0`** — the remote server is enabled and network-exposed in the `hybrid` and `replacer_l1` topologies: [5](#0-4) 

**The corrupted reference flows directly into proposal validation**

`is_proposal_init_valid` calls `get_l1_prices_in_fri_and_wei`, which queries the same `L1GasPriceProvider` instance, and uses the result as the locally-trusted anchor against which the proposer's claimed `l1_gas_price_fri`, `l1_data_gas_price_fri`, `l1_gas_price_wei`, and `l1_data_gas_price_wei` are checked: [6](#0-5) 

In a distributed deployment the consensus orchestrator (both proposer and validator roles) connects to the same shared `L1GasPriceProvider` service. Corrupting that single service therefore shifts the reference for every node simultaneously, so the proposer's injected prices remain within the validator's margin and the proposal is accepted.

---

### Impact Explanation

An attacker who can reach the `L1GasPriceProvider` port:

1. Sends `L1GasPriceRequest::Initialize` → ring buffer is wiped.
2. Sends a sequence of `L1GasPriceRequest::AddGasPrice` entries with attacker-chosen `base_fee_per_gas` / `blob_fee` values (e.g., near-zero).
3. The proposer's `build_proposal` path reads these values and embeds them in `ProposalInit.l1_gas_price_fri` / `l1_data_gas_price_fri`.
4. The validator's `is_proposal_init_valid` queries the same corrupted provider; the proposer's prices are within the margin of the attacker-controlled reference, so validation passes.
5. `decision_reached` commits the block with the attacker-chosen gas prices.

Committed blocks carry wrong L1 gas prices, directly satisfying **"Critical. Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact."**

---

### Likelihood Explanation

The `L1GasPriceProvider` remote server is deployed with `bind_ip: "0.0.0.0"` in production topologies. Any process that can reach the configured port — including any pod in the same Kubernetes cluster, or any external host if network policies are absent or misconfigured — can issue the attack. No credentials, tokens, or special privileges are required; a correctly serialized `L1GasPriceRequest` is sufficient.

The only partial mitigation is the consecutive-block-number check in `add_price_info`, but this is bypassed by first calling `Initialize`.

---

### Recommendation

1. **Separate write and read interfaces.** Expose only `GetGasPrice`, `GetEthToFriRate`, and `GetStrkToUsdRate` on the remote server. Route `AddGasPrice` and `Initialize` exclusively through the local (in-process) channel that the `L1GasPriceScraper` already uses.

2. **If a remote write path is required**, add mutual TLS or a shared-secret header check inside `remote_component_server_handler` before forwarding write-variant requests to the local client.

3. **Alternatively**, split `L1GasPriceRequest` into a read-only trait (consumed by consensus) and a write-only trait (consumed by the scraper), and instantiate two separate servers — one per trait — on different ports with different network exposure.

---

### Proof of Concept

```
# 1. Reset the provider's ring buffer
POST http://<l1-gas-price-provider-host>:<port>/
Content-Type: application/octet-stream
Body: bincode-serialized L1GasPriceRequest::Initialize

# 2. Inject fake near-zero gas prices for consecutive blocks
POST http://<l1-gas-price-provider-host>:<port>/
Content-Type: application/octet-stream
Body: bincode-serialized L1GasPriceRequest::AddGasPrice(GasPriceData {
    block_number: <current_l1_block>,
    timestamp: BlockTimestamp(<current_unix_ts>),
    price_info: PriceInfo {
        base_fee_per_gas: GasPrice(1),
        blob_fee: GasPrice(1),
    },
})

# Repeat step 2 for block+1, block+2, … to fill the mean window.
# The consensus orchestrator now reads GasPrice(1) as the trusted reference.
# A proposer embedding GasPrice(1) in ProposalInit passes is_proposal_init_valid
# on every validator that shares this provider instance.
```

The `RemoteComponentServer` accepts these requests without any authentication check, as confirmed by the handler at: [7](#0-6)

### Citations

**File:** crates/apollo_infra/src/component_server/remote_component_server.rs (L186-235)
```rust
    #[instrument(skip_all, fields(request_id = %request_id, remote_addr = %client_peer))]
    async fn remote_component_server_handler(
        http_request: HyperRequest<Incoming>,
        request_id: RequestId,
        client_peer: SocketAddr,
        local_client: LocalComponentClient<Request, Response>,
        metrics: &'static RemoteServerMetrics,
        max_request_body_bytes: usize,
    ) -> Result<HyperResponse<Full<Bytes>>, hyper::Error> {
        trace!("Received HTTP request: {http_request:?}");
        let body_bytes =
            match Limited::new(http_request.into_body(), max_request_body_bytes).collect().await {
                Ok(collected) => collected.to_bytes(),
                Err(err) => {
                    warn!("Request body too large: {err}");
                    let server_error = ServerError::RequestBodyTooLarge(err.to_string());
                    return Ok(HyperResponse::builder()
                        .status(StatusCode::PAYLOAD_TOO_LARGE)
                        .header(CONTENT_TYPE, APPLICATION_OCTET_STREAM)
                        .body(Full::new(Bytes::from(
                            SerdeWrapper::new(server_error)
                                .wrapper_serialize()
                                .expect("Server error serialization should succeed"),
                        )))
                        .expect("Response building should succeed"));
                }
            };
        trace!("Extracted {} bytes from HTTP request body", body_bytes.len());

        metrics.increment_total_received();

        let http_response = match SerdeWrapper::<Request>::wrapper_deserialize(&body_bytes)
            .map_err(|err| ClientError::ResponseDeserializationFailure(err.to_string()))
        {
            Ok(request) => {
                trace!(
                    remote_addr = %client_peer,
                    request_id = %request_id,
                    request_type = request.request_label(),
                    "remote component request",
                );
                trace!("Successfully deserialized request: {request:?}");
                metrics.increment_valid_received();

                // Wrap the send operation in a tokio::spawn as it is NOT a cancel-safe operation.
                // Even if the current task is cancelled, the inner task will continue to run.
                // Note: this creates a new request ID for the local client.
                let response = tokio::spawn(async move { local_client.send(request).await })
                    .await
                    .expect("Should be able to extract value from the task");
```

**File:** crates/apollo_l1_gas_price/src/communication.rs (L20-39)
```rust
#[async_trait]
impl ComponentRequestHandler<L1GasPriceRequest, L1GasPriceResponse> for L1GasPriceProvider {
    #[instrument(skip(self))]
    async fn handle_request(&mut self, request: L1GasPriceRequest) -> L1GasPriceResponse {
        match request {
            L1GasPriceRequest::Initialize => L1GasPriceResponse::Initialize(self.initialize()),
            L1GasPriceRequest::GetGasPrice(timestamp) => {
                L1GasPriceResponse::GetGasPrice(self.get_price_info(timestamp))
            }
            L1GasPriceRequest::AddGasPrice(data) => {
                L1GasPriceResponse::AddGasPrice(self.add_price_info(data))
            }
            L1GasPriceRequest::GetEthToFriRate(timestamp) => {
                L1GasPriceResponse::GetEthToFriRate(self.eth_to_fri_rate(timestamp).await)
            }
            L1GasPriceRequest::GetStrkToUsdRate(timestamp) => {
                L1GasPriceResponse::GetStrkToUsdRate(self.strk_to_usd_rate(timestamp).await)
            }
        }
    }
```

**File:** crates/apollo_l1_gas_price_types/src/lib.rs (L69-75)
```rust
pub enum L1GasPriceRequest {
    Initialize,
    GetGasPrice(BlockTimestamp),
    AddGasPrice(GasPriceData),
    GetEthToFriRate(u64),
    GetStrkToUsdRate(u64),
}
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L96-121)
```rust
    pub fn initialize(&mut self) -> L1GasPriceProviderResult<()> {
        info!("Initializing L1GasPriceProvider with config: {:?}", self.config);
        self.price_samples_by_block = Some(RingBuffer::new(self.config.storage_limit));
        Ok(())
    }

    pub fn add_price_info(&mut self, new_data: GasPriceData) -> L1GasPriceProviderResult<()> {
        // In case the provider has been restarted while the scraper is still running,
        // a NotInitializedError will be returned to the scraper. We expect the scraper to exit with
        // an error, and that infrastructure will restart it, leading to initialization.
        let Some(samples) = &mut self.price_samples_by_block else {
            return Err(L1GasPriceProviderError::NotInitializedError);
        };
        if let Some(data) = samples.back() {
            if new_data.block_number != data.block_number + 1 {
                return Err(L1GasPriceProviderError::UnexpectedBlockNumberError {
                    expected: data.block_number + 1,
                    found: new_data.block_number,
                });
            }
        }
        trace!("Received price sample for L1 block: {:?}", new_data);
        info_every_n_ms!(1_000, "Received price sample for L1 block: {:?}", new_data);
        samples.push(new_data);
        Ok(())
    }
```

**File:** crates/apollo_deployments/resources/services/hybrid/l1.json (L61-69)
```json
  "components.l1_gas_price_provider.max_concurrency": 128,
  "components.l1_gas_price_provider.port": 1,
  "components.l1_gas_price_provider.remote_client_config.#is_none": true,
  "components.l1_gas_price_provider.remote_server_config.#is_none": false,
  "components.l1_gas_price_provider.remote_server_config.bind_ip": "0.0.0.0",
  "components.l1_gas_price_provider.remote_server_config.max_streams_per_connection": 8,
  "components.l1_gas_price_provider.remote_server_config.set_tcp_nodelay": true,
  "components.l1_gas_price_provider.url": "remote_service",
  "components.l1_gas_price_scraper.execution_mode": "Enabled",
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L322-329)
```rust
    let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
        l1_gas_price_provider,
        init_proposed.timestamp,
        proposal_init_validation.previous_proposal_init.as_ref(),
        gas_price_params,
    )
    .await;
    let l1_gas_price_margin_percent =
```
