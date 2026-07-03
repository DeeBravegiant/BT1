### Title
Unrestricted `CREATE3Factory.deploy()` Allows Any Caller to Permanently Block Deterministic Deployment Addresses - (File: contracts/utils/CREATE3Factory.sol)

### Summary
`CREATE3Factory.deploy()` has no access control. Any external caller can pre-occupy any salt-derived address by deploying arbitrary bytecode there first. Because `CREATE3.deploy()` uses `create2` for the proxy step and `create2` reverts when the target address is already occupied, the legitimate protocol deployer is permanently blocked from using that salt.

### Finding Description
`CREATE3Factory` is a public factory intended to deploy protocol contracts at the same deterministic address across multiple chains. [1](#0-0) 

The `deploy` function carries no `onlyOwner`, role check, or any other access restriction. It delegates directly to `CREATE3.deploy()`: [2](#0-1) 

Inside `CREATE3.deploy()`, the first step is a `create2` call that deploys a minimal proxy at the address derived from `(address(this), salt, PROXY_BYTECODE_HASH)`. If any contract already exists at that proxy address, `create2` returns `address(0)` and the call reverts with `"DEPLOYMENT_FAILED"`. [3](#0-2) 

The final deployed address is also fully deterministic and publicly computable via `getDeployed(salt)`: [4](#0-3) 

An attacker who observes (or predicts) the salt the protocol intends to use can call `CREATE3Factory.deploy(salt, <garbage_bytecode>)` first, permanently occupying the proxy address. All subsequent legitimate calls with the same salt will revert.

### Impact Explanation
The factory is explicitly designed for cross-chain deterministic deployments ("This factory can be deployed at the same address on multiple chains"). [5](#0-4) 

If an attacker blocks a salt on one or more chains, the protocol cannot deploy the intended contract at the expected address on those chains. Cross-chain address consistency is broken, and any off-chain or on-chain component that hard-codes or pre-computes the expected address will fail to interact with the correct contract. The occupation is permanent — `create2` cannot redeploy to an already-occupied address.

**Impact: Low** — Contract fails to deliver promised (deterministic) deployment addresses; no direct fund loss, but cross-chain deployment guarantees are violated.

### Likelihood Explanation
Salts are either chosen off-chain (and thus observable in mempool or governance proposals) or computed deterministically from public parameters. An attacker can compute the target proxy address off-chain using `getDeployed(salt)` and front-run the deployment transaction. No special privilege is required.

**Likelihood: Medium** — Requires only mempool observation and a single transaction; no privileged access needed.

### Recommendation
Add an access-control modifier to `CREATE3Factory.deploy()` so only authorized deployers (e.g., a protocol multisig or a role-gated address) can call it:

```solidity
// Example fix
address public immutable owner;
constructor(address _owner) { owner = _owner; }

function deploy(bytes32 salt, bytes memory creationCode)
    external payable returns (address deployed)
{
    require(msg.sender == owner, "NOT_AUTHORIZED");
    deployed = CREATE3.deploy(salt, creationCode, msg.value);
    emit ContractDeployed(salt, deployed);
}
```

### Proof of Concept
1. Protocol announces (on-chain or off-chain) intent to deploy `ContractX` using `CREATE3Factory` with `salt = keccak256("ContractX_v1")`.
2. Attacker calls `CREATE3Factory.getDeployed(salt)` to compute the target address.
3. Attacker calls `CREATE3Factory.deploy(salt, hex"6000")` (minimal valid bytecode) — succeeds, occupying the proxy address.
4. Protocol calls `CREATE3Factory.deploy(salt, type(ContractX).creationCode)` — reverts with `"DEPLOYMENT_FAILED"` because the proxy address is already occupied.
5. The salt is permanently burned; the protocol must choose a new salt and update all systems that expected the original deterministic address. [1](#0-0) [6](#0-5)

### Citations

**File:** contracts/utils/CREATE3Factory.sol (L8-9)
```text
/// @dev This factory can be deployed at the same address on multiple chains
contract CREATE3Factory {
```

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

**File:** contracts/utils/CREATE3.sol (L53-65)
```text
    function getDeployed(bytes32 salt) internal view returns (address) {
        address proxy = keccak256(abi.encodePacked(bytes1(0xFF), address(this), salt, PROXY_BYTECODE_HASH)).
            // Prefix:
            // Creator:
            // Salt:
            // Bytecode hash:
            fromLast20Bytes();

        return keccak256(abi.encodePacked(hex"d694", proxy, hex"01")) // Nonce of the proxy contract (1)
            // 0xd6 = 0xc0 (short RLP prefix) + 0x16 (length of: 0x94 ++ proxy ++ 0x01)
            // 0x94 = 0x80 + 0x14 (0x14 = the length of an address, 20 bytes, in hex)
            .fromLast20Bytes();
    }
```
