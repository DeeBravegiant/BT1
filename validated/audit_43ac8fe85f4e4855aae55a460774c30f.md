### Title
Stale `rsETHPrice` Allows Depositors to Capture Accrued Yield at Existing Holders' Expense - (File: contracts/LRTDepositPool.sol)

### Summary
The `rsETHPrice` stored in `LRTOracle` is not updated atomically with TVL changes. When staking rewards accrue and TVL increases, the stored price lags behind the true price. Any depositor can exploit this window by depositing before `updateRSETHPrice()` is called, receiving more rsETH than they deserve and effectively stealing accrued yield from existing holders.

### Finding Description
`LRTOracle` stores `rsETHPrice` as a state variable that must be explicitly updated via the public `updateRSETHPrice()` function. [1](#0-0) 

This stored price is consumed directly by `LRTDepositPool.getRsETHAmountToMint()` to determine how many rsETH tokens to mint per deposit: [2](#0-1) 

Neither `depositETH()` nor `depositAsset()` calls `updateRSETHPrice()` before computing the mint amount: [3](#0-2) 

When staking rewards accrue (EigenLayer rewards, LST price appreciation), `_getTotalEthInProtocol()` would return a higher value than the last recorded `rsETHPrice` reflects. Because `rsETHPrice` is the denominator in the mint calculation, a stale (lower) `rsETHPrice` causes the depositor to receive **more rsETH than the true exchange rate warrants**. [4](#0-3) 

The `updateRSETHPrice()` function is publicly callable by anyone, meaning an attacker can:
1. Observe that TVL has increased (rewards accrued) without the stored price being updated.
2. Deposit at the stale (lower) price, receiving excess rsETH.
3. Call `updateRSETHPrice()` to finalize the price increase.
4. Hold rsETH worth more than the ETH deposited. [5](#0-4) 

The withdrawal path does **not** mirror this vulnerability — `_calculatePayoutAmount` uses `min(expectedAssetAmount, currentReturn)`, which caps the payout at the lower of the locked or current value: [6](#0-5) 

The deposit path has no equivalent protection.

### Impact Explanation
The excess rsETH minted to the attacker dilutes all existing holders. After `updateRSETHPrice()` is called, the price per rsETH rises to reflect accrued rewards, but the attacker's oversized position means existing holders receive a smaller share of those rewards. This is a direct transfer of unclaimed yield from existing rsETH holders to the attacker. The magnitude scales with deposit size and the duration of oracle staleness.

**Concrete example:**
- 1,000 rsETH outstanding; 1,050 ETH in protocol (rewards accrued); stored `rsETHPrice` = 1.00 ETH (stale); true price = 1.05 ETH.
- Attacker deposits 1 ETH → receives `1 / 1.00 = 1` rsETH (correct would be `1 / 1.05 ≈ 0.952` rsETH).
- After price update: new price = `1051 / 1001 ≈ 1.04995` ETH.
- Attacker's 1 rsETH is worth ≈ 1.04995 ETH (profit ≈ 0.05 ETH).
- Existing holders' 1,000 rsETH are each worth ≈ 1.04995 ETH instead of 1.05 ETH — a collective loss of ≈ 0.05 ETH transferred to the attacker.

### Likelihood Explanation
Staking rewards accrue continuously. `updateRSETHPrice()` is not called automatically in any deposit path. There is always a non-zero window between reward accrual and the next oracle update. The attack is permissionless, requires no special role, and can be executed by any depositor who monitors on-chain TVL (e.g., EigenLayer strategy balances) against the stored `rsETHPrice`.

### Recommendation
Call `updateRSETHPrice()` (or an internal equivalent) at the start of `depositETH()` and `depositAsset()` before computing `rsethAmountToMint`, ensuring the price reflects the current TVL before any new rsETH is minted.

### Proof of Concept
1. Rewards accrue in EigenLayer; `_getTotalEthInProtocol()` would return a value higher than `rsethSupply * rsETHPrice`.
2. Attacker calls `LRTDepositPool.depositETH{value: X}(0, "")` — `rsETHPrice` is stale (lower than true value), so `rsethAmountToMint = X * assetPrice / staleRsETHPrice` is inflated.
3. Attacker calls `LRTOracle.updateRSETHPrice()` — price rises to reflect accrued rewards.
4. Attacker holds rsETH worth more than X ETH; existing holders' per-token value is reduced by the dilution. [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L212-316)
```text
    /// @dev Internal function to update rsETH price
    // solhint-disable-next-line code-complexity
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
        }

        // downside protection — pause if price drops too far
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }

            // if price has decreased compared to the previous price, emit an event to reflect that
            if (previousPrice > newRsETHPrice) {
                emit RsETHPriceDecrease(newRsETHPrice, previousPrice);
            }

            // emit an event to notify that the price is currently below the peak (all time high) price
            emit RsETHPriceBelowPeak(highestRsethPrice, newRsETHPrice);
        }

        // update highest price if new price exceeds it
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }

        // mint protocol fee as rsETH if there's a fee to take
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
        } else {
            _checkAndUpdateDailyFeeMintLimit(0);
        }

        rsETHPrice = newRsETHPrice;

        emit RsETHPriceUpdate(rsETHPrice, previousPrice);
    }
```

**File:** contracts/LRTOracle.sol (L329-349)
```text
    /// @notice get total ETH in protocol
    /// @return totalETHInProtocol total ETH in protocol (normalized to 1e18)
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```

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

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

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

**File:** contracts/LRTWithdrawalManager.sol (L824-835)
```text
    function _calculatePayoutAmount(
        WithdrawalRequest storage request,
        uint256 rsETHPrice,
        uint256 assetPrice
    )
        private
        view
        returns (uint256)
    {
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
    }
```
