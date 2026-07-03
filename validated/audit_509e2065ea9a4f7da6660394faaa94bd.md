### Title
Permissionless `updateRate()` Enables Profitable Deposits Against Stale Cross-Chain Rate — (`contracts/cross-chain/CrossChainRateProvider.sol`, `contracts/cross-chain/MultiChainRateProvider.sol`)

---

### Summary

Both `CrossChainRateProvider` and `MultiChainRateProvider` expose a permissionless `updateRate()` function that broadcasts the current L1 `rsETHPrice()` to L2 receiver contracts via LayerZero. L2 pool contracts (`RSETHPoolV3`, `RSETHPoolNoWrapper`, etc.) use the **stale cached rate** stored in `CrossChainRateReceiver` to calculate how much rsETH to mint per deposited ETH. An unprivileged attacker can observe the L1 price increase, deposit at the stale lower rate to receive excess rsETH, then trigger the rate update themselves, extracting yield from the protocol.

---

### Finding Description

**Rate broadcast is permissionless.** `CrossChainRateProvider.updateRate()` and `MultiChainRateProvider.updateRate()` carry no access control — they are `external payable nonReentrant` with no `onlyOwner` or role check: [1](#0-0) [2](#0-1) 

**L1 rate source is public.** `RSETHRateProvider.getLatestRate()` and `RSETHMultiChainRateProvider.getLatestRate()` both read `ILRTOracle(rsETHPriceOracle).rsETHPrice()`, a public view function on L1: [3](#0-2) [4](#0-3) 

**L2 receiver stores a stale cached rate.** `CrossChainRateReceiver` stores the last received rate in `rate` storage, updated only when `lzReceive` is called. Between updates, `getRate()` returns the stale value: [5](#0-4) 

**L2 pools use this stale rate for minting.** `RSETHPoolNoWrapper.getRate()` and `RSETHPoolV3.getRate()` delegate directly to `IOracle(rsETHOracle).getRate()`, which resolves to the stale `CrossChainRateReceiver.rate`. The deposit functions use this to compute `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate`: [6](#0-5) [7](#0-6) [8](#0-7) 

For `RSETHPoolV3`, the minted token is `wrsETH.mint(msg.sender, rsETHAmount)` — new supply is created: [9](#0-8) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

When `rsETHPrice` on L1 has risen above the stale `CrossChainRateReceiver.rate` on L2, the formula `rsETHAmount = amountAfterFee * 1e18 / staleRate` produces **more rsETH than the deposited ETH is worth at the true rate**. For `RSETHPoolV3`, this excess rsETH is freshly minted, diluting all existing rsETH holders. The attacker's surplus rsETH represents yield that belongs to existing stakers but is instead captured by the attacker. The profit scales linearly with deposit size and the magnitude of the rate lag.

---

### Likelihood Explanation

**High.** The L1 oracle price (`LRTOracle.rsETHPrice()`) and the L2 cached rate (`CrossChainRateReceiver.rate`) are both public on-chain state. Any actor can compute the discrepancy at any time without mempool monitoring. Because `updateRate()` is permissionless, the attacker controls the exact timing of the rate push — they can deposit at the stale rate and immediately trigger the update in the same block or the next, making the attack deterministic and repeatable after every reward accrual event on L1.

---

### Recommendation

1. **Restrict `updateRate()`** in both `CrossChainRateProvider` and `MultiChainRateProvider` to a trusted role (e.g., `onlyOwner` or a dedicated `RATE_UPDATER_ROLE`), preventing an attacker from controlling the timing of rate propagation.
2. **Add a minimum deposit lock-up or redemption delay** on L2 pools so that rsETH minted cannot be immediately sold against a freshly updated rate in the same block.
3. **Bound the acceptable rate delta** in `CrossChainRateReceiver.lzReceive()` — reject updates that increase the rate by more than a configurable threshold per period, consistent with the `pricePercentageLimit` guard already present in `LRTOracle._updateRsETHPrice()` on L1. [10](#0-9) 

---

### Proof of Concept

1. Attacker reads `LRTOracle.rsETHPrice()` on L1 → returns `1.05e18` (rsETH has appreciated).
2. Attacker reads `CrossChainRateReceiver.rate` on L2 → returns `1.04e18` (stale, not yet updated).
3. Attacker calls `RSETHPoolV3.deposit{value: 100 ether}("")` on L2.
   - `viewSwapRsETHAmountAndFee(100e18)` computes: `rsETHAmount = 100e18 * 1e18 / 1.04e18 ≈ 96.15 rsETH`
   - At the true rate: `100e18 * 1e18 / 1.05e18 ≈ 95.24 rsETH`
   - **Excess minted: ~0.91 rsETH** (≈ 0.96 ETH of value at current rate).
4. Attacker calls `RSETHRateProvider.updateRate{value: lzFee}()` on L1 — permissionless, no revert.
5. LayerZero delivers the message; `CrossChainRateReceiver.rate` is updated to `1.05e18`.
6. Attacker sells the 96.15 rsETH on a Balancer pool or DEX that prices rsETH using `CrossChainRateReceiver.getRate()` at the new `1.05e18` rate, recovering `≈ 100.96 ETH` — a net profit of `≈ 0.96 ETH` on a 100 ETH deposit, minus fees and gas.

### Citations

**File:** contracts/cross-chain/CrossChainRateProvider.sol (L85-101)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        bytes memory remoteAndLocalAddresses = abi.encodePacked(rateReceiver, address(this));

        rate = latestRate;

        lastUpdated = block.timestamp;

        bytes memory _payload = abi.encode(latestRate);

        ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(
            dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
        );

        emit RateUpdated(rate);
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

**File:** contracts/cross-chain/RSETHRateProvider.sol (L27-28)
```text
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
```

**File:** contracts/cross-chain/RSETHMultiChainRateProvider.sol (L26-28)
```text
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
    }
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-105)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
    }

    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L219-222)
```text
    /// @dev Gets the rate from the rsETHOracle
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L277-286)
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

**File:** contracts/pools/RSETHPoolV3.sol (L286-292)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
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

**File:** contracts/LRTOracle.sol (L252-266)
```text
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
```
