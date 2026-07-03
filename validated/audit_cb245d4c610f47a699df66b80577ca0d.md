### Title
wrsETH Minted on L2 Without Locking rsETH in `RsETHTokenWrapper`, Causing Persistent Undercollateralization and Temporary Fund Freeze - (File: contracts/pools/RSETHPoolV2ExternalBridge.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV2NBA.sol, contracts/pools/RSETHPoolV2.sol)

---

### Summary

Every L2 pool contract mints `wrsETH` to depositors immediately upon ETH receipt, but the `RsETHTokenWrapper` that backs `wrsETH` with real `rsETH` is only funded asynchronously by an off-chain bridger. This creates a persistent window where `wrsETH` total supply exceeds the `rsETH` balance held in the wrapper, making the wrapper insolvent for any user who attempts to redeem `wrsETH` for `rsETH` before the bridger completes the round-trip.

---

### Finding Description

The L2 deposit pools call `wrsETH.mint(msg.sender, rsETHAmount)` directly on the `RsETHTokenWrapper` contract the moment a user deposits ETH: [1](#0-0) [2](#0-1) [3](#0-2) 

The `mint()` entry point on `RsETHTokenWrapper` mints `wrsETH` with **no rsETH deposited into the wrapper**: [4](#0-3) 

The only way rsETH enters the wrapper is via `depositBridgerAssets()`, which is called by a privileged `BRIDGER_ROLE` operator **after** the ETH has been bridged to L1, deposited into `LRTDepositPool`, rsETH minted, and then bridged back to L2: [5](#0-4) 

The `withdraw()` path burns `wrsETH` and transfers `rsETH` from the wrapper's own balance: [6](#0-5) 

Because `wrsETH` is minted before any `rsETH` is locked, the wrapper is **always undercollateralized** between the time of deposit and the time the bridger completes the round-trip. The deficit is tracked by:

```
maxAmountToDepositBridgerAsset(asset) = totalSupply() - rsETH.balanceOf(wrapper)
``` [7](#0-6) 

This is the direct analog to the Lido L2 issue: stETH was minted without locking wstETH; here, `wrsETH` is minted without locking `rsETH`.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

Any user who holds `wrsETH` and calls `withdraw()` on the `RsETHTokenWrapper` during the undercollateralization window will have their transaction revert because the wrapper holds insufficient `rsETH`. In a worst-case scenario (bridging failure, LayerZero message drop, L1Vault misconfiguration), the wrapper remains permanently undercollateralized and the freeze becomes permanent — escalating to **Critical (permanent freezing of funds)**.

Even in the normal case, the wrapper is structurally insolvent at all times between user deposits and bridger settlement. A user who deposits and immediately tries to redeem via `withdraw()` will always fail.

---

### Likelihood Explanation

**High.** Every single ETH deposit through any of the pool contracts (`RSETHPoolV2ExternalBridge`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV2NBA`, `RSETHPoolV2`) triggers the mismatch. No special conditions are required. The undercollateralization window is a permanent structural feature of the protocol's design. Any user can observe it and any user attempting to use the `withdraw()` path during the window is affected.

---

### Recommendation

Mirror the recommendation from the Lido report: instead of minting `wrsETH` speculatively and relying on the bridger to backfill rsETH, the protocol should ensure that `wrsETH` is only minted once the corresponding `rsETH` has been received and locked in the `RsETHTokenWrapper`. Concretely:

1. **Do not call `wrsETH.mint()` at deposit time.** Instead, issue a receipt/IOU to the depositor.
2. **Mint `wrsETH` only after `depositBridgerAssets()` is called** with the corresponding `rsETH`, using the `_deposit()` path (which locks rsETH 1:1 before minting).
3. Alternatively, enforce that `withdraw()` can only be called up to the amount of `rsETH` currently held in the wrapper, and document the undercollateralization risk prominently.

---

### Proof of Concept

1. Alice calls `RSETHPoolV2ExternalBridge.deposit{value: 10 ether}("")`.
   - Pool computes `rsETHAmount` via oracle rate.
   - Pool calls `wrsETH.mint(Alice, rsETHAmount)`.
   - `RsETHTokenWrapper` mints `wrsETH` to Alice. **No rsETH is locked.**
   - `RsETHTokenWrapper.totalSupply()` = 10 wrsETH; `rsETH.balanceOf(wrapper)` = 0.

2. Bob also calls `deposit{value: 10 ether}("")`.
   - Same flow. `totalSupply()` = 20 wrsETH; `rsETH.balanceOf(wrapper)` = 0.

3. Bridger bridges 10 ETH to L1, L1Vault mints rsETH, bridges 10 rsETH back to L2, calls `depositBridgerAssets(rsETH, 10e18)`.
   - `rsETH.balanceOf(wrapper)` = 10 rsETH. Still 10 wrsETH unbacked.

4. Alice calls `RsETHTokenWrapper.withdraw(rsETH, Alice, 10e18)`.
   - Burns 10 wrsETH, transfers 10 rsETH to Alice. Wrapper now has 0 rsETH.

5. Bob calls `RsETHTokenWrapper.withdraw(rsETH, Bob, 10e18)`.
   - **Reverts** — wrapper has 0 rsETH but Bob holds 10 wrsETH.
   - Bob's funds are frozen until the bridger deposits the remaining 10 rsETH. [1](#0-0) [4](#0-3) [6](#0-5) [5](#0-4)

### Citations

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L289-300)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L366-384)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV2NBA.sol (L106-118)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L100-120)
```text
        if (!allowedTokens[_asset]) return 0;

        // get totalSupply of wrsETH minted
        uint256 wrsETHSupply = totalSupply();
        // balance of _asset with the contract
        uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

        if (balanceOfAssetInWrapper > wrsETHSupply) return 0;

        return wrsETHSupply - balanceOfAssetInWrapper;
    }

    /*//////////////////////////////////////////////////////////////
                           INTERNAL FUNCTIONS
    //////////////////////////////////////////////////////////////*/

    /// @dev Withdraw altRsETH tokens from wrsETH
    /// @param _asset The address of the token to withdraw
    /// @param _to The user to withdraw to
    /// @param _amount The amount of tokens to withdraw
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L121-128)
```text
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L157-170)
```text
    /// @dev Legacy function - Deposit for when the rsETH is bridged by the
    /// bridger from L1 so as to collateralize already minted wrsETH on L2
    ///
    /// @param _asset The address of the token to deposit
    /// @param _amount The amount of tokens to deposit
    function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
        if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
            revert CannotDeposit();
        }

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        emit BridgerDeposited(_asset, msg.sender, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L187-192)
```text
    /// @dev Mint wrsETH tokens on L2
    /// @param _to The address to mint the tokens to
    /// @param _amount The amount of tokens to mint
    function mint(address _to, uint256 _amount) external onlyRole(MINTER_ROLE) {
        _mint(_to, _amount);
    }
```
