### Title
Unprotected `CREATE3Factory.deploy()` Allows Any Caller to Pre-empt Protocol Deployments at Deterministic Addresses - (File: contracts/utils/CREATE3Factory.sol)

### Summary
`CREATE3Factory.deploy()` carries no access control, meaning any external account can occupy any deterministic address derived from an arbitrary salt before the protocol does. This is the direct analog of the FraxlendPairDeployer bug: a predictable salt-based deployment mechanism where an adversary can permanently seize the target address, either blocking the legitimate deployment or installing malicious bytecode at the pre-announced address.

### Finding Description
`CREATE3Factory.deploy(bytes32 salt, bytes memory creationCode)` is declared `external payable` with no role check or caller restriction.

```solidity
// contracts/utils/CREATE3Factory.sol
function deploy(bytes32 salt, bytes memory creationCode) external payable returns (address deployed) {
    deployed = CREATE3.deploy(salt, creationCode, msg.value);
    emit ContractDeployed(salt, deployed);
}
```

Internally, `CREATE3.deploy` issues a `create2` call using the raw `salt` with no `msg.sender` component:

```solidity
// contracts/utils/CREATE3.sol
proxy := create2(0, add(proxyChildBytecode, 32), mload(proxyChildBytecode), salt)
```

Because CREATE3 derives the final contract address solely from `(factory_address, salt)`, any two callers supplying the same salt will target the identical proxy address. The first caller permanently occupies it; the second call reverts with `"DEPLOYMENT_FAILED"`.

The factory's own NatSpec states it is designed to be deployed at the same address on multiple chains, meaning the protocol relies on deterministic, pre-computable addresses. Salts used by the protocol are therefore predictable from public information (chain ID, contract name, deployment sequence, etc.).

An attacker can:
1. Derive the salt the protocol will use for an upcoming deployment (from documentation, governance proposals, or mempool observation).
2. Call `CREATE3Factory.deploy(predictedSalt, maliciousCreationCode)` before the protocol's transaction executes.
3. The proxy address is now permanently occupied by the attacker's bytecode.
4. The protocol's legitimate deployment reverts.
5. Any contract or user that references the pre-announced address now interacts with the attacker-controlled contract.

### Impact Explanation
**Minimum impact (Medium — Temporary freezing of funds):** The protocol cannot deploy a critical contract (pool, oracle, bridge adapter) at the expected deterministic address. Cross-chain address consistency is broken. Funds already routed to the pre-announced address (e.g., via counterfactual interactions or pre-funded vaults) are inaccessible until the protocol redeploys under a different salt and migrates state.

**Maximum impact (Critical — Direct theft of user funds):** If the attacker installs malicious bytecode at the pre-announced address (e.g., a fake `RSETHPool` or `RsETHTokenWrapper`), users who deposit ETH or LSTs into that address lose their funds. The factory is used to deploy L2 pool contracts and wrappers that directly receive user assets.

### Likelihood Explanation
Medium. The factory is designed for cross-chain deterministic deployment, so salts are necessarily predictable and reproducible. No whitelist or role is required — any EOA can call `deploy()`. The attacker does not need to front-run the mempool; they only need to know the salt before the protocol's transaction is mined, which is achievable from public governance or deployment documentation.

### Recommendation
1. **Add access control**: Restrict `deploy()` to a designated deployer role (e.g., `onlyOwner` or a `DEPLOYER_ROLE`).
2. **Namespace salts by caller**: Compute the effective salt as `keccak256(abi.encodePacked(msg.sender, salt))` inside `CREATE3.deploy`, so different callers cannot collide on the same address.
3. **Commit-reveal**: Require deployers to commit a hash of `(salt, creationCode)` in a prior transaction before revealing and deploying, preventing mempool-based pre-emption.

### Proof of Concept

```
// Attacker observes that the protocol will deploy RSETHPoolV3 with:
//   salt = keccak256("RSETHPoolV3_Arbitrum_v1")
// from the CREATE3Factory deployed at 0x81E5c1483...

// Step 1: Attacker pre-computes the target address
address target = CREATE3Factory(factory).getDeployed(keccak256("RSETHPoolV3_Arbitrum_v1"));

// Step 2: Attacker deploys malicious pool at that address
CREATE3Factory(factory).deploy(
    keccak256("RSETHPoolV3_Arbitrum_v1"),
    type(MaliciousPool).creationCode   // drains deposited ETH to attacker
);
// target now holds attacker bytecode

// Step 3: Protocol's deployment reverts
CREATE3Factory(factory).deploy(
    keccak256("RSETHPoolV3_Arbitrum_v1"),
    type(RSETHPoolV3).creationCode
);
// → "DEPLOYMENT_FAILED"

// Step 4: Users who deposit ETH into `target` lose funds
MaliciousPool(target).deposit{value: 10 ether}();
// ETH transferred to attacker
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/utils/CREATE3Factory.sol (L17-20)
```text
    function deploy(bytes32 salt, bytes memory creationCode) external payable returns (address deployed) {
        deployed = CREATE3.deploy(salt, creationCode, msg.value);
        emit ContractDeployed(salt, deployed);
    }
```

**File:** contracts/utils/CREATE3.sol (L37-51)
```text
    function deploy(bytes32 salt, bytes memory creationCode, uint256 value) internal returns (address deployed) {
        bytes memory proxyChildBytecode = PROXY_BYTECODE;

        address proxy;
        assembly {
            // Deploy a new contract with our pre-made bytecode via CREATE2.
            // We start 32 bytes into the code to avoid copying the byte length.
            proxy := create2(0, add(proxyChildBytecode, 32), mload(proxyChildBytecode), salt)
        }
        require(proxy != address(0), "DEPLOYMENT_FAILED");

        deployed = getDeployed(salt);
        (bool success,) = proxy.call{ value: value }(creationCode);
        require(success && deployed.code.length != 0, "INITIALIZATION_FAILED");
    }
```
