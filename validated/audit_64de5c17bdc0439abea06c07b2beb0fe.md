The code at lines 676-682 of `contracts/LRTDepositPool.sol` exactly matches the claim. The asymmetry is confirmed:

- ETH branch: `totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)` — `amount` omitted [1](#0-0) 
- ERC20 branch: `totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)` — `amount` included [2](#0-1) 

`depositETH` is a public payable function reachable by any user. [3](#0-2) 

SECURITY.md contains no exclusion applicable to this finding. The exploit requires no privileges, no oracle manipulation, and no external dependencies.

---

Audit Report

## Title
ETH Deposit Limit Not Enforced on Incoming Amount — (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` omits the incoming `amount` from the ETH branch limit check, using `totalAssetDeposits > limit` instead of `totalAssetDeposits + amount > limit`. Any unprivileged depositor can push total ETH deposits above the admin-configured cap the moment deposits reach exactly the limit, while the equivalent ERC20 path correctly reverts.

## Finding Description
`_checkIfDepositAmountExceedesCurrentLimit` (L676–682) is the sole enforcement gate for per-asset deposit caps. For ETH it evaluates `totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)`, ignoring `amount`. For ERC20 it evaluates `totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)`.

When `totalAssetDeposits == limit`:
- ETH branch: `limit > limit` → `false` → `_beforeDeposit` does not revert → `_mintRsETH` executes → `totalAssetDeposits` becomes `limit + msg.value`.
- ERC20 branch: `limit + amount > limit` → `true` → `MaximumDepositLimitReached` revert.

`_beforeDeposit` (L648–670) calls this check unconditionally and is invoked by `depositETH` (L76–93), a public `payable` function with no access restriction beyond `whenNotPaused` and `onlySupportedAsset`. No existing guard compensates for the missing `amount` in the ETH branch.

## Impact Explanation
The deposit limit is a protocol safety invariant. Bypassing it allows rsETH to be minted beyond the admin-configured cap. Because rsETH price is recalculated from actual TVL on every `updateRSETHPrice` call, no direct fund loss or insolvency results; however the protocol silently violates its own accounting invariant. This maps to **Low — contract fails to deliver promised returns, but doesn't lose value**.

## Likelihood Explanation
Any ETH depositor can trigger this the moment `getTotalAssetDeposits(ETH_TOKEN)` equals the configured limit. No special privilege, front-running, or timing attack is required. The condition arises in normal operation as the cap fills and is repeatable on every subsequent deposit while the cap remains unupdated.

## Recommendation
Include `amount` in the ETH branch to match ERC20 logic:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 1000 ether`.
2. Protocol accumulates exactly `1000 ether` in total ETH deposits.
3. Alice calls `depositETH{value: 100 ether}(minRSETHAmountExpected, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 100 ether)` evaluates `1000 ether > 1000 ether` → `false` → no revert.
5. `_mintRsETH` mints rsETH for Alice; total ETH deposits become `1100 ether`, 10% above the cap.
6. Foundry test: deploy with a mock `lrtConfig` returning `depositLimitByAsset = 1000 ether` and `getTotalAssetDeposits = 1000 ether`; assert `depositETH{value: 1}()` succeeds and assert `depositAsset(erc20, 1, 0)` reverts with `MaximumDepositLimitReached` — demonstrating the asymmetry.

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

**File:** contracts/LRTDepositPool.sol (L678-679)
```text
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
```

**File:** contracts/LRTDepositPool.sol (L681-681)
```text
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
```
