Audit Report

## Title
ETH Deposit Limit Not Enforced — Incoming Amount Excluded from Cap Check - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` uses an asymmetric comparison: for ERC-20 assets it correctly evaluates `totalAssetDeposits + amount > depositLimit`, but for ETH it evaluates only `totalAssetDeposits > depositLimit`, silently discarding the incoming `amount`. Any unprivileged caller can invoke `depositETH` with an arbitrarily large `msg.value` and bypass the `depositLimitByAsset` cap entirely, as long as the pre-existing total has not already crossed the limit.

## Finding Description
The root cause is in `_checkIfDepositAmountExceedesCurrentLimit` at lines 676–682:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // amount unused
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [1](#0-0) 

The ETH branch asks only "has the limit already been exceeded?" — never "would this deposit exceed the limit?" The `amount` parameter (equal to `msg.value`) is received but never used in the ETH branch.

The full call chain is public and unprivileged:

- `depositETH` (lines 76–93): `external payable nonReentrant whenNotPaused` — no role check. [2](#0-1) 
- `_beforeDeposit` (lines 648–670): calls `_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)` and reverts with `MaximumDepositLimitReached` only if it returns `true`. For ETH, it never returns `true` unless the pre-existing total already exceeds the cap. [3](#0-2) 

No existing guard compensates for the missing `amount` in the ETH branch.

## Impact Explanation
The `depositLimitByAsset` cap is the protocol's primary risk-management control over how much ETH is restaked. Bypassing it allows rsETH to be minted beyond the intended ceiling. Excess ETH accumulates idle in the deposit pool and cannot be deployed to EigenLayer strategies until governance intervenes. This matches **Low — Contract fails to deliver promised returns, but doesn't lose value**: the deposit-cap guarantee is broken, but deposited ETH is not directly stolen.

## Likelihood Explanation
`depositETH` is public and requires no special role. Any depositor who observes `totalAssetDeposits < depositLimitByAsset(ETH)` can send an arbitrarily large single transaction. No front-running, privileged access, or victim mistake is required. The condition is trivially observable on-chain and the exploit is repeatable until governance raises or enforces the cap.

## Recommendation
Include the incoming deposit amount in the ETH branch, mirroring the ERC-20 logic:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

Additionally, `getAssetCurrentLimit` (lines 402–409) uses `>` instead of `>=`, so it reports a non-zero remaining limit even when the cap is exactly met; change the comparison to `>=` for consistency. [4](#0-3) 

## Proof of Concept
Assume `depositLimitByAsset(ETH) = 100_000 ether` and `totalAssetDeposits(ETH) = 99_999 ether`.

1. Attacker calls `depositETH{value: 500_000 ether}(0, "")`.
2. `_checkIfDepositAmountExceedesCurrentLimit(ETH, 500_000 ether)` evaluates `99_999 ether > 100_000 ether` → `false` → no revert.
3. `getRsETHAmountToMint` mints rsETH for the full 500,000 ETH.
4. `totalAssetDeposits(ETH)` becomes 599,999 ETH — nearly 6× the intended cap — with no protocol-level rejection.

**Foundry test sketch:**
```solidity
function test_ethDepositBypassesLimit() public {
    // set depositLimitByAsset(ETH) = 100_000 ether, seed pool with 99_999 ether
    vm.deal(attacker, 500_000 ether);
    vm.prank(attacker);
    depositPool.depositETH{value: 500_000 ether}(0, "");
    assertGt(depositPool.getTotalAssetDeposits(ETH_TOKEN), 100_000 ether);
}
```

### Citations

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L402-409)
```text
    function getAssetCurrentLimit(address asset) public view override returns (uint256) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
            return 0;
        }

        return lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;
    }
```

**File:** contracts/LRTDepositPool.sol (L661-663)
```text
        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }
```

**File:** contracts/LRTDepositPool.sol (L676-682)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```
