### Title
Cross-Contract Reentrancy via ERC777 Token Callback in `depositAsset` Allows Inflated rsETH Withdrawal Locking - (File: contracts/LRTDepositPool.sol)

---

### Summary
`LRTDepositPool.depositAsset` violates the Checks-Effects-Interactions pattern: it calculates `rsethAmountToMint` and then calls `safeTransferFrom` (external call) before calling `_mintRsETH` (state update). If a supported asset is ERC777-compatible, the `tokensReceived` hook fires after the transfer but before the rsETH mint. During this window, the rsETH price is transiently inflated (assets increased, rsETH supply unchanged). The `nonReentrant` guard on `LRTDepositPool` does **not** block cross-contract calls to `LRTWithdrawalManager.initiateWithdrawal`, allowing an attacker to lock in a withdrawal at the inflated price.

---

### Finding Description

In `LRTDepositPool.depositAsset`:

```solidity
// checks
uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

// interactions
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);  // ← external call
_mintRsETH(rsethAmountToMint);                                              // ← state update AFTER
``` [1](#0-0) 

The rsETH mint (`_mintRsETH`) is the critical state update that increases rsETH supply and normalises the rsETH/ETH price. It is placed **after** the external `safeTransferFrom` call. If the deposited token implements ERC777 (or any `tokensReceived`-style hook), the hook fires after the transfer completes but before `_mintRsETH` executes. At that moment:

- The deposit pool's asset balance has **increased** by `depositAmount`.
- The rsETH total supply has **not yet increased**.
- The rsETH price (= total ETH value of assets / rsETH supply) is therefore **transiently inflated**.

The `nonReentrant` modifier on `LRTDepositPool` only prevents re-entry into functions on that same contract. It does **not** prevent the attacker's hook from calling `LRTWithdrawalManager.initiateWithdrawal`, which is a separate contract with its own independent reentrancy guard. [2](#0-1) 

`LRTWithdrawalManager.initiateWithdrawal` calls `getExpectedAssetAmount(asset, rsETHUnstaked)` to compute the withdrawal amount at the **current** oracle price. Because the oracle price is derived from total protocol assets divided by rsETH supply, it reads the transiently inflated price during the hook window. [3](#0-2) 

---

### Impact Explanation

**Critical — Direct theft of user funds.**

An attacker who already holds rsETH can lock in a withdrawal commitment (`assetsCommitted`) at an inflated rsETH/asset exchange rate. After the hook returns and `_mintRsETH` executes, the price normalises. When the attacker later calls `completeWithdrawal`, they receive more underlying assets than their rsETH entitles them to at the true price. The excess comes from other depositors' assets, constituting direct fund theft.

---

### Likelihood Explanation

**Low-to-Medium.** The attack requires a supported LST asset to implement ERC777 callbacks (or a similar `tokensReceived` hook). Current mainnet-supported assets (stETH, ETHx, sfrxETH) are standard ERC20 tokens without such hooks. However, `LRTConfig` allows new assets to be added by governance, and the `onlySupportedERC20Token` modifier does not exclude ERC777-compatible tokens. Any future addition of an ERC777-compatible LST (e.g., a wrapped or rebasing token with hooks) would immediately expose this path to any unprivileged depositor who also holds rsETH.

---

### Recommendation

1. **Move `_mintRsETH` before `safeTransferFrom`**, or alternatively move the rsETH amount calculation to after the transfer (reading the actual received balance delta), so that the rsETH supply is updated before any external call completes.
2. **Add a cross-contract reentrancy guard** or document that `LRTWithdrawalManager.initiateWithdrawal` must not be callable while `LRTDepositPool` is mid-execution.
3. **Explicitly reject ERC777 tokens** in the asset-addition governance path, or add a global reentrancy lock shared across `LRTDepositPool` and `LRTWithdrawalManager`.

The corrected CEI order should be:

```solidity
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount); // transfer first
uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected); // recalculate after
_mintRsETH(rsethAmountToMint); // mint last
```

---

### Proof of Concept

1. Attacker holds `X` rsETH. Protocol has `T` total ETH-value assets and `S` rsETH supply. Normal price = `T/S`.
2. Attacker calls `LRTDepositPool.depositAsset(erc777Token, D, ...)` where `erc777Token` is a supported ERC777 asset.
3. `_beforeDeposit` calculates `rsethAmountToMint` for the attacker's own deposit.
4. `safeTransferFrom` executes; `D` worth of assets enter the pool. The ERC777 `tokensReceived` hook fires on the attacker's contract.
5. **Inside the hook**: attacker calls `LRTWithdrawalManager.initiateWithdrawal(asset, X, ...)`. Oracle reads price = `(T+D)/S` (inflated). Withdrawal commitment is recorded for `X * (T+D)/S / assetPrice` units of `asset`. `assetsCommitted[asset]` is incremented at the inflated amount.
6. Hook returns. `_mintRsETH` executes, minting rsETH for the attacker's deposit. Price returns to `(T+D)/(S + rsethAmountToMint)`.
7. After `withdrawalDelayBlocks`, attacker calls `completeWithdrawal` and receives the inflated asset amount, draining funds from other users. [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }
```
