### Title
ETH Deposit Limit Bypass Due to Missing `msg.value` in Limit Check - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` enforces the deposit cap correctly for ERC20 LSTs but omits the incoming `msg.value` from the ETH branch check, allowing any single ETH deposit to push total ETH holdings arbitrarily above the configured `depositLimitByAsset` cap.

### Finding Description
The function `_checkIfDepositAmountExceedesCurrentLimit` contains an asymmetric guard:

```solidity
// contracts/LRTDepositPool.sol L676-L682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← amount ignored
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
```

For ERC20 assets the check is `totalAssetDeposits + amount > limit` — the incoming deposit is included. For ETH the check is only `totalAssetDeposits > limit` — the incoming `msg.value` is silently dropped. The `amount` parameter (which equals `msg.value` at the call site in `_beforeDeposit`) is never added to `totalAssetDeposits` before the comparison.

The call chain is:

`depositETH` → `_beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, ...)` → `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, msg.value)` → ETH branch ignores `msg.value`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
The `depositLimitByAsset` cap for ETH is a protocol-level risk management control stored in `LRTConfig`. [4](#0-3)  It is the only on-chain mechanism preventing the protocol from accepting more ETH than intended. Because the ETH branch of the limit check never adds the incoming deposit to the running total, the cap is never actually enforced for ETH: a depositor can send an arbitrarily large ETH amount in a single transaction as long as `totalAssetDeposits` has not yet crossed the limit. The excess ETH is minted as rsETH and held in the deposit pool or forwarded to NodeDelegators, but EigenLayer pod capacity and operator delegation limits may not accommodate the overflow, leading to ETH sitting idle and unable to be staked — a temporary freeze of deposited funds. Additionally, the rsETH exchange rate is computed against total TVL, so inflating ETH holdings beyond the intended cap distorts the rate for all existing holders.

**Impact class**: Medium — Temporary freezing of funds / contract fails to deliver promised returns.

### Likelihood Explanation
The entry point `depositETH` is public, payable, and requires no special role. Any depositor who observes that `totalAssetDeposits` for ETH is close to but below the limit can send a single transaction with `msg.value` far exceeding the remaining headroom. No splitting, no flash loan, and no privileged access is required. The condition is trivially detectable on-chain via `getAssetCurrentLimit`. [5](#0-4) 

### Recommendation
Add `amount` to `totalAssetDeposits` in the ETH branch, mirroring the ERC20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
Assume `depositLimitByAsset[ETH] = 100 ether` and `getTotalAssetDeposits(ETH) = 99 ether`.

1. Attacker calls `depositETH{value: 1000 ether}(0, "")`.
2. `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 1000 ether)`.
3. ETH branch evaluates `99 ether > 100 ether` → `false` → limit not exceeded.
4. `_mintRsETH` mints rsETH for 1000 ETH; total ETH in protocol becomes 1099 ETH, 999 ETH above the intended cap.
5. The excess ETH cannot be deployed to EigenLayer if pod/strategy capacity is exhausted, leaving it frozen in the deposit pool or NodeDelegators. [1](#0-0)

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

**File:** contracts/LRTDepositPool.sol (L648-663)
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

**File:** contracts/LRTConfig.sol (L23-23)
```text
    mapping(address token => uint256 amount) public depositLimitByAsset;
```
