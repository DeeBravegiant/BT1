### Title
Deflationary/Fee-on-Transfer Token Mis-Accounting Inflates rsETH Minting in `depositAsset` - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool.depositAsset()` calculates the rsETH amount to mint using the caller-supplied `depositAmount` parameter before the `transferFrom` executes, then mints that full amount regardless of how many tokens were actually received. For fee-on-transfer or deflationary tokens, the actual received balance is less than `depositAmount`, causing the protocol to mint more rsETH than is backed by real assets.

### Finding Description
In `depositAsset()`, the flow is:

1. `rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected)` — computes rsETH to mint using the raw `depositAmount` argument.
2. `IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount)` — transfers tokens; for a fee-on-transfer token, the contract receives `depositAmount - fee`, not `depositAmount`.
3. `_mintRsETH(rsethAmountToMint)` — mints rsETH calculated from the full `depositAmount`, not the actual received amount.

The actual balance increase is never measured. The contract unconditionally trusts the caller-supplied amount.

`_beforeDeposit` delegates to `getRsETHAmountToMint`:

```
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

This uses `amount = depositAmount` (the stated value), not the actual tokens received.

### Impact Explanation
**Critical — Direct theft of user funds.**

A depositor using a fee-on-transfer supported LST receives rsETH backed by `depositAmount` worth of assets but only `depositAmount - fee` tokens are actually held by the protocol. The rsETH supply is inflated relative to actual backing. When this depositor (or any early redeemer) withdraws, they drain assets that belong to other depositors. The shortfall is borne by remaining rsETH holders, constituting direct theft of at-rest funds.

### Likelihood Explanation
**Medium.** The vulnerability is latent: it activates the moment any fee-on-transfer ERC20 is added to the supported asset list via governance. The `lrtConfig` asset registry is admin-controlled, and the deposit function itself has no guard against fee-on-transfer tokens. No attacker capability beyond being a normal depositor is required once such a token is listed.

### Recommendation
Measure the actual received amount by comparing the contract's balance before and after `transferFrom`, and use that delta for rsETH minting:

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

uint256 rsethAmountToMint = _beforeDeposit(asset, actualReceived, minRSETHAmountExpected);
_mintRsETH(rsethAmountToMint);
```

The same fix should be applied to `RsETHTokenWrapper._deposit()` and `AGETHTokenWrapper._deposit()`.

### Proof of Concept

**Root cause — rsETH minted from stated amount, not actual received:** [1](#0-0) 

**`_beforeDeposit` computes mint amount from the raw `depositAmount` argument:** [2](#0-1) 

**`getRsETHAmountToMint` uses `amount` directly with no balance check:** [3](#0-2) 

**Same pattern in `RsETHTokenWrapper._deposit` — mints 1:1 to stated `_amount` after `safeTransferFrom`:** [4](#0-3) 

**Same pattern in `AGETHTokenWrapper._deposit`:** [5](#0-4) 

**Attack path:**
1. A fee-on-transfer token (e.g., 1% fee) is added as a supported LST asset.
2. Attacker calls `depositAsset(feeToken, 1000e18, 0, "")`.
3. Protocol receives `990e18` tokens but mints rsETH equivalent to `1000e18`.
4. Attacker redeems rsETH, withdrawing assets that include the `10e18` shortfall taken from other depositors' backing.
5. Repeated deposits amplify the insolvency until the protocol cannot honor all redemptions.

### Citations

**File:** contracts/LRTDepositPool.sol (L110-117)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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

**File:** contracts/L2/RsETHTokenWrapper.sol (L134-141)
```text
    function _deposit(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
        emit Deposit(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L125-132)
```text
    function _deposit(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
        emit Deposit(_asset, _to, _amount);
    }
```
