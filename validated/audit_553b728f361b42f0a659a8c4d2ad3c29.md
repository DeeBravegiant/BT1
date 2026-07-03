### Title
Unpermissioned Salt in `CREATE3Factory.deploy()` Enables Reorg-Based Fund Theft via Address Hijacking - (File: contracts/utils/CREATE3Factory.sol)

### Summary

`CREATE3Factory.deploy()` accepts a caller-supplied `salt` without namespacing it by `msg.sender`. Because the final deployed address is fully determined by `(factory_address, salt)`, any actor can race to claim any salt during a block reorganization, deploying arbitrary bytecode to an address a legitimate deployer had already pre-funded.

### Finding Description

The `CREATE3Factory` is a permissionless factory: any EOA or contract may call `deploy(salt, creationCode)` with any salt value. [1](#0-0) 

Internally, `CREATE3.deploy` first deploys a fixed-bytecode proxy via `CREATE2` keyed on `(address(this), salt, PROXY_BYTECODE_HASH)`, then the proxy uses the `CREATE` opcode (nonce = 1) to deploy the real contract. [2](#0-1) 

The final address is therefore deterministic on `(factory_address, salt)` alone: [3](#0-2) 

Because `msg.sender` is never mixed into the salt, two different callers using the same `salt` value target the same deployment address. During a reorg, an attacker who observes a pending `deploy` transaction can replay a competing transaction with identical `salt` but malicious `creationCode`, winning the race and controlling the contract at the address the legitimate deployer expected.

### Impact Explanation

**Critical — direct theft of user funds.**

The counterfactual deployment pattern (pre-funding an address before the contract is deployed) is a standard use of CREATE3. If a protocol actor or user sends tokens/ETH to `getDeployed(salt)` before the deployment transaction is confirmed, and a reorg allows an attacker to deploy malicious bytecode to that same address first, the attacker gains full control of those funds and can drain them immediately.

### Likelihood Explanation

**Low.** Exploitation requires a block reorganization of sufficient depth on the target chain. The protocol targets any EVM-compatible network; Polygon and Optimistic rollups have documented multi-block reorgs. The attacker must also have a monitoring bot and act within the reorg window. However, the CREATE3Factory is permissionless, so no privileged access is needed beyond timing.

### Recommendation

Namespace the salt by `msg.sender` inside `CREATE3Factory.deploy()` so that no two callers can ever target the same deployment address:

```solidity
function deploy(bytes32 salt, bytes memory creationCode) external payable returns (address deployed) {
    bytes32 guardedSalt = keccak256(abi.encodePacked(msg.sender, salt));
    deployed = CREATE3.deploy(guardedSalt, creationCode, msg.value);
    emit ContractDeployed(salt, deployed);
}

function getDeployed(bytes32 salt) external view returns (address) {
    bytes32 guardedSalt = keccak256(abi.encodePacked(msg.sender, salt));
    return CREATE3.getDeployed(guardedSalt);
}
```

This mirrors the recommendation from the Sablier report and ensures address derivation is caller-specific.

### Proof of Concept

1. Alice calls `CREATE3Factory.getDeployed(saltA)` off-chain, obtains address `X`, and sends 100 rsETH to `X` in anticipation of deploying her contract there.
2. Alice submits `CREATE3Factory.deploy(saltA, legitimateBytecode)`.
3. A reorg occurs. Bob's bot detects the pending transaction.
4. Bob submits `CREATE3Factory.deploy(saltA, maliciousBytecode)` with higher gas, landing first in the reorganized chain.
5. Bob's malicious contract is now deployed at address `X`.
6. Alice's 100 rsETH, already sitting at `X`, is immediately drained by Bob's contract.
7. Alice's original `deploy` transaction reverts because the proxy address for `saltA` is already occupied. [1](#0-0) [4](#0-3)

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
