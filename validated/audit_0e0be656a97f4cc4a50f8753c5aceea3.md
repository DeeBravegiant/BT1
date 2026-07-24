Looking at the production code in `evm/src/omni-bridge/contracts/HlBridgeToken.sol`, I need to verify:
1. Whether `data` is user-controlled in the HyperCore→HyperEVM flow
2. Whether `_update(from, address(0), amount)` burns in OZ ERC20
3. Whether there is any zero-address guard on the recipient

### Title
Missing Zero-Address Recipient Validation in `ACTION_TRANSFER` Burns Pool Tokens — (`evm/src/omni-bridge/contracts/HlBridgeToken.sol`)

---

### Summary

`HyperliquedBridgeToken.coreReceiveWithData` accepts user-controlled `data` from HyperCore via the system address relay. In the `ACTION_TRANSFER` branch, the decoded `recipient` is passed directly to `_update(_systemAddress, recipient, amount)` with no zero-address guard. OpenZeppelin's `ERC20Upgradeable._update` treats `to == address(0)` as a burn, permanently destroying pool tokens and creating an unbacked supply discrepancy between HyperCore and HyperEVM.

---

### Finding Description

The `coreReceiveWithData` function is the HyperCore→HyperEVM callback. Its NatSpec explicitly states it is "invoked by the system address when a **HyperCore user** triggers `sendToEvmWithData`", meaning the `data` bytes are fully attacker-controlled. [1](#0-0) 

The only access control is `msg.sender == _systemAddress`, which is satisfied because the system address is the relay — it faithfully forwards whatever `data` the HyperCore user supplied. [2](#0-1) 

In the `ACTION_TRANSFER` branch, the decoded `recipient` is used without any zero-address check: [3](#0-2) 

OpenZeppelin `ERC20Upgradeable._update(from, address(0), value)` is the canonical burn path: it decrements `from`'s balance and decrements `totalSupply` without reverting. There is no override of `_update` in either `BridgeToken` or `HyperliquedBridgeToken` that would add a guard. [4](#0-3) 

The grep search across all production contracts in `evm/src/omni-bridge/contracts/` confirms there is no zero-address check on `recipient` anywhere.

The accounting model parks all HyperCore-side tokens at `_systemAddress` via the 3-arg `mint`: [5](#0-4) 

So `_systemAddress` holds the entire HyperCore-side backing pool. Burning from it destroys EVM-side supply while HyperCore-side balances remain intact.

---

### Impact Explanation

- `totalSupply` decreases by `amount`; no tokens are credited to any address.
- The `_systemAddress` pool — which mirrors total HyperCore-side balance — is drained.
- HyperCore users who later call `sendToEvmWithData` with a valid recipient will find the pool insufficient and revert (`ERC20InsufficientBalance`), permanently locking their HyperCore-side funds with no redemption path.
- The discrepancy is irreversible: there is no mechanism to re-mint destroyed supply without a privileged operator action.

This satisfies: **Critical — irreversible fund lock / permanently unclaimable user value** and **Critical — unauthorized destruction of bridge-backing supply**.

---

### Likelihood Explanation

- Requires only a HyperCore account (zero privilege).
- The attacker calls `sendToEvmWithData` with `data = 0x00 ++ abi.encode(address(0))` and any `amount ≤ pool balance`.
- The pool is non-zero whenever any HyperCore-side minting has occurred.
- The attack is repeatable up to the full pool balance, and can be executed in a single transaction.
- No special timing, no MEV, no key material needed.

Likelihood: **High**.

---

### Recommendation

Add a zero-address guard immediately after decoding the recipient in the `ACTION_TRANSFER` branch:

```solidity
if (action == ACTION_TRANSFER) {
    address recipient = abi.decode(tail, (address));
    if (recipient == address(0)) revert InvalidRecipient();   // ADD THIS
    _update(_systemAddress, recipient, amount);
}
```

Add a corresponding custom error:
```solidity
error InvalidRecipient();
```

---

### Proof of Concept

```solidity
// Precondition: pool seeded by a prior 3-arg mint
token.connect(adminAccount)["mint(address,uint256,bytes)"](anyUser, AMOUNT, "0x");
// _systemAddress.balance == AMOUNT, totalSupply == AMOUNT

// Attacker (any HyperCore user) triggers sendToEvmWithData with:
bytes memory data = abi.encodePacked(
    uint8(0),                        // ACTION_TRANSFER
    abi.encode(address(0))           // recipient = address(0)
);
// System address relays the call:
token.connect(systemSigner).coreReceiveWithData(
    attacker, bytes32(0), 0, AMOUNT, 0, data
);

// Result:
assert(token.totalSupply() == 0);                    // supply destroyed
assert(token.balanceOf(address(0)) == 0);            // no tokens credited
assert(token.balanceOf(SYSTEM_ADDRESS) == 0);        // pool drained
// HyperCore still records AMOUNT tokens for users → unbacked
``` [3](#0-2)

### Citations

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L76-83)
```text
    function mint(
        address account,
        uint256 value,
        bytes memory
    ) external override onlyOwner {
        _mint(account, value);
        _update(account, _systemAddress, value);
    }
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L85-86)
```text
    /// @notice HyperCore -> HyperEVM callback invoked by the system address when a
    /// HyperCore user triggers `sendToEvmWithData` targeting this token.
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L114-114)
```text
        if (msg.sender != _systemAddress) revert NotSystemAddress();
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L120-122)
```text
        if (action == ACTION_TRANSFER) {
            address recipient = abi.decode(tail, (address));
            _update(_systemAddress, recipient, amount);
```

**File:** evm/src/omni-bridge/contracts/BridgeToken.sol (L10-16)
```text
contract BridgeToken is
    Initializable,
    UUPSUpgradeable,
    ERC20Upgradeable,
    Ownable2StepUpgradeable,
    IBridgeToken
{
```
