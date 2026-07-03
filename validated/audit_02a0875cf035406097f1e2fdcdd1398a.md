Audit Report

## Title
Stale Cross-Chain rsETH Rate Accepted Without Staleness Check Leads to Protocol Insolvency - (File: contracts/cross-chain/CrossChainRateReceiver.sol)

## Summary
`CrossChainRateReceiver.getRate()` unconditionally returns the stored `rate` without checking `lastUpdated`, despite `lastUpdated` being recorded on every update. All L2 deposit pools (`RSETHPoolV3`, `RSETHPoolV2`, `RSETHPoolNoWrapper`) use this rate to compute how many wrsETH/rsETH tokens to mint or transfer per deposited ETH. Because rsETH appreciates monotonically, a stale (too-low) rate causes pools to over-issue tokens relative to the rsETH that will actually be received when bridged ETH is deposited on L1, creating a permanent insolvency shortfall.

## Finding Description

**Root cause:** `CrossChainRateReceiver.getRate()` stores `lastUpdated` on every `lzReceive()` call but never consults it in `getRate()`:

```solidity
// CrossChainRateReceiver.sol L95-105
rate = _rate;
lastUpdated = block.timestamp;   // recorded but never checked
...
function getRate() external view returns (uint256) {
    return rate;   // no staleness guard
}
``` [1](#0-0) 

**Rate update mechanism:** `MultiChainRateProvider.updateRate()` is permissionless but requires the caller to pay LayerZero fees and is not automated. There is no keeper, no incentive, and no on-chain enforcement of a minimum update frequency. [2](#0-1) 

**Exploit path in RSETHPoolV3:** `deposit()` calls `viewSwapRsETHAmountAndFee()`, which calls `getRate()` → `IOracle(rsETHOracle).getRate()` → stale `CrossChainRateReceiver.getRate()`. The minting formula is:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [3](#0-2) 

A stale (too-low) `rsETHToETHrate` inflates `rsETHAmount`. The deposited ETH is later bridged to L1 and deposited into `LRTDepositPool` at the actual (higher) current rate, producing fewer rsETH tokens than the wrsETH already minted on L2. [4](#0-3) 

The same pattern exists in `RSETHPoolV2.deposit()` and `RSETHPoolNoWrapper.deposit()`. [5](#0-4) [6](#0-5) 

**Existing checks are insufficient:** `ChainlinkOracleForRSETHPoolCollateral` — used for collateral-token prices in the same pools — explicitly reverts on stale data (`answeredInRound < roundID`, `timestamp == 0`). No equivalent guard exists for the rsETH/ETH rate from `CrossChainRateReceiver`. [7](#0-6) 

The SECURITY.md exclusion for "incorrect data supplied by third-party oracles" does not apply here: `CrossChainRateReceiver` is part of this repository, and the data it received was correct at the time of receipt — the bug is the absence of an on-chain staleness check in the protocol's own oracle. [8](#0-7) 

## Impact Explanation
Every deposit made while the rate is stale mints more wrsETH than the deposited ETH can redeem in rsETH. The L1Vault converts ETH to rsETH at the real (higher) rate, producing fewer rsETH tokens than the outstanding wrsETH supply. The shortfall is permanent and accumulates across all deposits during the staleness window. This constitutes **Critical — Protocol insolvency**.

## Likelihood Explanation
`updateRate()` requires an off-chain actor to pay LayerZero fees with no on-chain enforcement. rsETH accrues staking rewards continuously, so the true rate always drifts upward from any stale snapshot. Any unprivileged depositor can exploit this passively by depositing whenever the on-chain rate lags the true rate — no special access, front-running, or governance capture is required. During periods of low activity, network congestion, or LayerZero degradation, the staleness window can extend to hours or days.

## Recommendation
Add a configurable maximum staleness threshold to `CrossChainRateReceiver.getRate()` and revert if `block.timestamp - lastUpdated` exceeds it:

```solidity
uint256 public maxStaleness; // e.g. 24 hours

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
```

Additionally, automate `updateRate()` calls via a keeper or Chainlink Automation so the rate is refreshed at least once per reward epoch.

## Proof of Concept

**Setup:**
- L1 `LRTOracle.rsETHPrice()` = 1.05e18 (rsETH has appreciated 5% since last update)
- L2 `CrossChainRateReceiver.rate` = 1.00e18 (stale — last updated 48 hours ago)
- `CrossChainRateReceiver.lastUpdated` = 48 hours ago (ignored by `getRate()`)

**Execution:**
1. Attacker calls `RSETHPoolV3.deposit{value: 100 ether}("")`.
2. Pool calls `getRate()` → returns stale `1.00e18`.
3. `rsETHAmount = 100e18 * 1e18 / 1.00e18 = 100e18` wrsETH minted to attacker.
4. Bridger calls `moveAssetsForBridging()`, 100 ETH sent to L1Vault.
5. L1Vault calls `LRTDepositPool.depositETH()` at actual rate 1.05e18 → mints `≈95.24e18` rsETH.
6. 95.24 rsETH bridged back to L2 to back the wrsETH supply.
7. **Shortfall: 100 wrsETH outstanding, only 95.24 rsETH backing it — 4.76 rsETH deficit per 100 ETH deposited.**

**Foundry fork test plan:** Fork an L2 where `RSETHRateReceiver` is deployed. Warp block timestamp forward by 48 hours without calling `updateRate()`. Call `RSETHPoolV3.deposit{value: 100 ether}("")`. Assert that `wrsETH.balanceOf(attacker) > 100e18 * 1e18 / trueCurrentRate`, confirming over-minting relative to the actual L1 rsETH conversion.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-105)
```text
        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
    }

    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-137)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        rate = latestRate;

        lastUpdated = block.timestamp;

        bytes memory _payload = abi.encode(latestRate);

        uint256 rateReceiversLength = rateReceivers.length;

        for (uint256 i; i < rateReceiversLength;) {
            uint16 dstChainId = uint16(rateReceivers[i]._chainId);

            bytes memory remoteAndLocalAddresses = abi.encodePacked(rateReceivers[i]._contract, address(this));

            (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
                .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

            ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
                dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
            );

            unchecked {
                ++i;
            }
        }

        emit RateUpdated(rate);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L258-263)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

```

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L207-219)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-244)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }
```

**File:** SECURITY.md (L20-21)
```markdown
- Incorrect data supplied by third-party oracles.
- Impacts requiring basic economic and governance attacks (e.g. 51% attack).
```
