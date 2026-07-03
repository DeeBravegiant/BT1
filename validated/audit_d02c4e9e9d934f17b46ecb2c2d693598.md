### Title
ETH Deposit Limit Check Omits Incoming Amount, Allowing Limit Bypass - (File: contracts/LRTDepositPool.sol)

### Summary
`_checkIfDepositAmountExceedesCurrentLimit` in `LRTDepositPool.sol` uses an asymmetric check: for ERC20 tokens it correctly adds the incoming deposit amount to the running total before comparing against the limit, but for ETH it omits the incoming amount entirely. This means when `totalAssetDeposits == depositLimit`, the ETH branch returns `false` (not exceeded) and the deposit proceeds, pushing total ETH deposits past the configured cap.

### Finding Description
The root cause is in `_checkIfDepositAmountExceedesCurrentLimit`:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← `amount` missing
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
``` [1](#0-0) 

For ERC20 the check is `totalAssetDeposits + amount > limit` — the new deposit is included. For ETH the check is `totalAssetDeposits > limit` — the new deposit is absent. When `totalAssetDeposits == depositLimit`, the ETH branch evaluates `limit > limit` → `false`, so `_beforeDeposit` does not revert with `MaximumDepositLimitReached`, and the deposit is accepted. [2](#0-1) 

The entry path is `depositETH` → `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit`, callable by any unprivileged user. [3](#0-2) 

### Impact Explanation
The deposit limit is the protocol's primary on-chain cap on how much ETH can enter the system. When the limit is exactly reached, any depositor can still push one additional deposit through, causing `totalAssetDeposits` to exceed `depositLimitByAsset(ETH_TOKEN)` by up to `msg.value`. The protocol fails to deliver its promised deposit-cap guarantee for ETH.

**Impact: Low** — contract fails to deliver promised returns (deposit limit enforcement) without direct loss of funds.

### Likelihood Explanation
The condition `totalAssetDeposits == depositLimit` is a natural boundary that occurs as deposits accumulate toward the cap. Any depositor monitoring on-chain state can observe this moment and submit a deposit. No privileged access is required; the path is `depositETH` with any non-zero `msg.value`.

### Recommendation
Include the incoming `amount` in the ETH branch, mirroring the ERC20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
``` [1](#0-0) 

### Proof of Concept
1. `depositLimitByAsset(ETH_TOKEN)` is set to `100 ether`.
2. Through prior deposits, `getTotalAssetDeposits(ETH_TOKEN)` reaches exactly `100 ether`.
3. Attacker calls `depositETH(0, "ref")` with `msg.value = 1 ether`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 1 ether)` evaluates `100 ether > 100 ether` → `false`.
5. `_beforeDeposit` does not revert; rsETH is minted to the attacker.
6. `getTotalAssetDeposits(ETH_TOKEN)` is now `101 ether`, exceeding the configured limit.

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
