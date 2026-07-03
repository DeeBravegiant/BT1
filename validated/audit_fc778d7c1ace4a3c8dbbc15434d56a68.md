### Title
ETH Deposit Limit Not Enforced Per-Deposit Amount - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric check for ETH versus ERC20 tokens. For ETH, the check omits the incoming deposit `amount`, meaning a single deposit can push total ETH holdings arbitrarily far beyond the configured cap. For ERC20 tokens the check is correct. This is the direct analog of the Rubicon non-smooth-curve class: a boundary condition in the validation formula produces a result that diverges from the protocol's stated invariant depending on the input value.

### Finding Description

In `LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit`:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount ignored
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
``` [1](#0-0) 

The ETH branch evaluates `totalAssetDeposits > limit` — a strict-greater-than check on the **pre-deposit** total — without adding `amount`. The function returns `false` (i.e., "limit not exceeded") whenever the current total is at or below the cap, regardless of how large the incoming deposit is.

`depositETH` calls `_beforeDeposit`, which calls this function: [2](#0-1) [3](#0-2) 

So the full call chain is:

```
depositETH(msg.value)
  → _beforeDeposit(ETH_TOKEN, msg.value, minRSETH)
      → _checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, msg.value)
          → returns (totalAssetDeposits > limit)   // msg.value never added
```

### Impact Explanation

The deposit limit is the protocol's primary mechanism for capping ETH exposure to EigenLayer strategies. A single depositor can bypass it entirely:

- Suppose `depositLimit = 1 000 ETH` and `totalAssetDeposits = 999 ETH`.
- A user calls `depositETH` with `msg.value = 10 000 ETH`.
- The check evaluates `999 > 1000` → `false` → deposit proceeds.
- Total ETH in protocol becomes `10 999 ETH`, 10× the intended cap.

All excess ETH is minted as rsETH and eventually forwarded to EigenLayer node delegators, exposing the protocol to slashing risk far beyond what governance intended. If slashing occurs on the over-deposited amount, rsETH holders face losses that the deposit cap was designed to prevent.

**Impact category:** Low — Contract fails to deliver promised returns (deposit cap invariant broken); escalates to Medium/Critical if slashing materialises on the excess.

### Likelihood Explanation

The entry point `depositETH` is public and permissionless. No special role, whitelist, or precondition is required. Any depositor who observes that `totalAssetDeposits < depositLimit` can immediately exploit this with a single transaction. Likelihood is **High**.

### Recommendation

Include `amount` in the ETH branch, matching the ERC20 logic:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept

1. Deploy with `depositLimitByAsset[ETH_TOKEN] = 1_000 ether`.
2. Seed the pool so `getTotalAssetDeposits(ETH_TOKEN) = 999 ether`.
3. Call `depositETH{value: 10_000 ether}("")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `999e18 > 1_000e18` → `false`.
5. `_mintRsETH` mints rsETH for the full `10_000 ETH`.
6. `getTotalAssetDeposits(ETH_TOKEN)` now returns `10_999 ether`, 10× the cap. [1](#0-0)

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
