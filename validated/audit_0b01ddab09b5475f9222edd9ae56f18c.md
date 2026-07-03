The code is confirmed. The bug exists exactly as described.

- [1](#0-0)  — ETH branch omits `amount`, ERC20 branch includes it.
- [2](#0-1)  — `depositETH` is public, passes `msg.value` into `_beforeDeposit`.
- [3](#0-2)  — `_beforeDeposit` calls the check and reverts only if it returns `true`.
- [4](#0-3)  — `getAssetCurrentLimit` correctly accounts for remaining capacity, misleading integrators.

---

Audit Report

## Title
ETH Deposit Limit Check Missing `amount` Parameter Allows Limit Bypass - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` evaluates `totalAssetDeposits > limit` for ETH but `totalAssetDeposits + amount > limit` for ERC20 tokens. This asymmetry means any single ETH deposit that would cross the configured cap is silently accepted, allowing total ETH deposits to exceed the governance-set ceiling by an arbitrary amount. The public `depositETH` function is the direct entry point and requires no special privileges.

## Finding Description
In `contracts/LRTDepositPool.sol` at lines 676–682, the ETH branch of `_checkIfDepositAmountExceedesCurrentLimit` returns `(totalAssetDeposits > lrtConfig.depositLimitByAsset(asset))`, omitting the incoming `amount`. The ERC20 branch on line 681 correctly uses `totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)`.

The call chain is:
1. `depositETH` (L76–93) — public, payable, no role required — calls `_beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected)`.
2. `_beforeDeposit` (L648–670) calls `_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)` and reverts with `MaximumDepositLimitReached` only if it returns `true`.
3. Because `amount` is absent from the ETH branch, the guard returns `false` whenever `totalAssetDeposits ≤ limit`, regardless of how large `msg.value` is.
4. `_mintRsETH` proceeds, minting rsETH for the full over-limit deposit.

`getAssetCurrentLimit` (L402–409) correctly computes remaining capacity as `limit - totalAssetDeposits`, so off-chain tooling and integrators see a non-zero remaining capacity right up to the moment the limit is crossed, giving no warning of the breach.

## Impact Explanation
The deposit limit is the protocol's primary governance-enforced safety cap on ETH exposure to EigenLayer. Bypassing it allows the protocol to accept more ETH than governance intended, exposing it to greater EigenLayer slashing risk than authorized. No ETH is directly stolen, but the protocol fails to enforce a core promised safety invariant. This matches **Low: Contract fails to deliver promised returns, but doesn't lose value**.

## Likelihood Explanation
No special role or privilege is required. Any external user calling `depositETH` with a `msg.value` large enough to push `totalAssetDeposits` above the limit triggers the bypass. The vulnerable condition (`totalAssetDeposits ≤ limit`) is the normal operating state of the protocol. The bypass is therefore reachable on every ordinary deposit that would otherwise be the limit-crossing deposit, with no attacker setup beyond holding ETH.

## Recommendation
Add `amount` to the ETH branch to match the ERC20 branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin sets `depositLimitByAsset(ETH_TOKEN) = 1000 ETH`.
2. Current state: `getTotalAssetDeposits(ETH) = 999 ETH`.
3. Alice calls `depositETH{value: 500 ETH}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 500e18)` evaluates `999e18 > 1000e18` → `false`.
5. `_beforeDeposit` does not revert; `_mintRsETH` mints rsETH for 500 ETH.
6. Post-call: `getTotalAssetDeposits(ETH) = 1499 ETH` — 499 ETH above the cap — with no revert or breach event.

**Foundry fuzz test plan:**
```solidity
function testFuzz_ETHDepositLimitBypass(uint256 depositAmount) public {
    vm.assume(depositAmount > remainingCapacity && depositAmount < type(uint128).max);
    // set limit, pre-fill to just below limit, then deposit over limit
    // assert: call succeeds AND getTotalAssetDeposits(ETH) > depositLimitByAsset(ETH)
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

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
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
