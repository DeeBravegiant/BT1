### Title
Stale `CrossChainRateReceiver` Rate Used in L2 Pools Mints Excess wrsETH, Undercollateralizing the Wrapper - (File: contracts/cross-chain/CrossChainRateReceiver.sol)

---

### Summary

The L2 deposit pools (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`) mint wrsETH using the rsETH/ETH rate stored in `CrossChainRateReceiver`, which is pushed from L1 via LayerZero and can become arbitrarily stale. No staleness check exists anywhere in the deposit path. Because rsETH price increases monotonically over time as staking rewards accrue, a stale rate is always lower than the current L1 rate. This causes the L2 pool to mint more wrsETH per ETH than the rsETH that will actually be received on L1 when the bridged ETH is deposited, undercollateralizing the wrsETH wrapper and diluting or freezing existing holders' funds.

---

### Finding Description

**Cross-chain rate propagation architecture:**

The rsETH/ETH exchange rate is computed on L1 by `LRTOracle._updateRsETHPrice()` and pushed to L2 via `RSETHMultiChainRateProvider.updateRate()` → LayerZero → `CrossChainRateReceiver.lzReceive()`. The receiver stores the rate and a `lastUpdated` timestamp but exposes no staleness enforcement:

```solidity
// CrossChainRateReceiver.sol:93-97
uint256 _rate = abi.decode(_payload, (uint256));
rate = _rate;
lastUpdated = block.timestamp;
emit RateUpdated(_rate);
```

`getRate()` simply returns the stored value:

```solidity
// CrossChainRateReceiver.sol:103-105
function getRate() external view returns (uint256) {
    return rate;
}
```

**L2 pool minting path:**

`RSETHPoolV3.deposit()` calls `viewSwapRsETHAmountAndFee()`, which divides by the oracle rate:

```solidity
// RSETHPoolV3.sol:304-307
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

Then mints wrsETH directly:

```solidity
// RSETHPoolV3.sol:262
wrsETH.mint(msg.sender, rsETHAmount);
```

`RSETHPoolV3ExternalBridge.deposit()` follows the identical pattern at lines 377–381.

**L1 minting path (after bridging):**

When the bridged ETH arrives at `L1Vault`, the manager calls `depositETHForL1VaultETH()`, which mints rsETH at the **current** L1 rate:

```solidity
// L1Vault.sol:152-158
uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH);
lrtDepositPool.depositETH{ value: balanceOfETH }(rsETHAmountToMint, "");
```

**The mismatch:**

| Step | Rate used | rsETH/wrsETH amount for 1 ETH |
|---|---|---|
| L2 wrsETH mint | stale rate `R_stale` (lower) | `1e18 / R_stale` (larger) |
| L1 rsETH mint | current rate `R_current` (higher) | `1e18 / R_current` (smaller) |

Since `R_stale < R_current`, the L2 pool mints more wrsETH than the rsETH the L1Vault will receive. The wrsETH wrapper on L2 ends up holding fewer rsETH than the wrsETH outstanding.

**No staleness check exists** anywhere in the deposit flow. `updateRate()` on the provider is callable by anyone but is never enforced, and there is no on-chain circuit breaker that pauses deposits when the rate is stale.

---

### Impact Explanation

The wrsETH wrapper on L2 becomes undercollateralized: it holds less rsETH than the wrsETH it has issued. Existing wrsETH holders who attempt to unwrap find insufficient rsETH backing their tokens. This constitutes:

- **Theft of unclaimed yield (High):** Depositors who exploit the stale rate receive wrsETH representing yield that has not yet been propagated to L2, effectively extracting value from existing holders whose rsETH backing is diluted.
- **Temporary freezing of funds (Medium):** Legitimate wrsETH holders cannot fully unwrap because the wrapper's rsETH balance is insufficient to cover all outstanding wrsETH.

---

### Likelihood Explanation

The rate becomes stale naturally whenever `updateRate()` is not called. Since rsETH accrues staking rewards continuously, even a few hours of staleness creates a measurable discrepancy. An attacker can:

1. Monitor `CrossChainRateReceiver.lastUpdated` on-chain.
2. Wait for a period of inactivity (or simply not call `updateRate()` themselves, since there is no obligation to do so).
3. Deposit ETH on L2 when the rate is maximally stale, receiving the largest possible excess wrsETH.

This requires no privileged access and is reachable by any unprivileged depositor.

---

### Recommendation

Add a staleness guard in `RSETHPoolV3.deposit()` and `RSETHPoolV3ExternalBridge.deposit()` (and their `viewSwapRsETHAmountAndFee` helpers) that reverts if `block.timestamp - CrossChainRateReceiver.lastUpdated > MAX_RATE_AGE`. Alternatively, expose `lastUpdated` through the oracle interface and enforce the check inside `getRate()` itself so all consumers are protected automatically.

---

### Proof of Concept

1. L1 rsETH price is `1.05e18` (current, after recent staking rewards).
2. `CrossChainRateReceiver.rate` is `1.00e18` (stale, last updated 48 hours ago).
3. Attacker deposits `1 ETH` on L2 via `RSETHPoolV3.deposit()`.
   - `rsETHAmount = 1e18 * 1e18 / 1.00e18 = 1.000e18` wrsETH minted.
   - Correct amount at current rate: `1e18 * 1e18 / 1.05e18 ≈ 0.952e18` wrsETH.
   - Excess minted: `≈ 0.048e18` wrsETH.
4. Bridger calls `bridgeAssets()` → 1 ETH arrives at `L1Vault`.
5. Manager calls `depositETHForL1VaultETH()` → L1Vault receives `≈ 0.952e18` rsETH at current rate.
6. `bridgeRsETHToL2()` sends `0.952e18` rsETH to the L2 wrapper.
7. Wrapper now holds `0.952e18` rsETH but has `1.000e18` wrsETH outstanding.
8. The `0.048e18` wrsETH shortfall means some holders cannot unwrap; the attacker has extracted `≈ 0.048 rsETH` worth of value from existing holders. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L82-100)
```text
    function lzReceive(uint16 _srcChainId, bytes memory _srcAddress, uint64, bytes calldata _payload) external {
        require(msg.sender == layerZeroEndpoint, "Sender should be lz endpoint");

        address srcAddress;
        assembly {
            srcAddress := mload(add(_srcAddress, 20))
        }

        require(_srcChainId == srcChainId, "Src chainId must be correct");
        require(srcAddress == rateProvider, "Src address must be provider");

        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
    }
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L258-264)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L377-384)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-427)
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

**File:** contracts/L1Vault.sol (L150-161)
```text
    function depositETHForL1VaultETH() external payable nonReentrant onlyRole(MANAGER_ROLE) {
        uint256 balanceOfETH = address(this).balance;
        uint256 rsETHAmountToMint = lrtDepositPool.getRsETHAmountToMint(ETH_IDENTIFIER, balanceOfETH);

        if (rsETHAmountToMint == 0) {
            revert InvalidMinRSETHAmountExpected();
        }

        lrtDepositPool.depositETH{ value: balanceOfETH }(rsETHAmountToMint, "");

        emit ETHDepositForL1Vault(balanceOfETH, rsETHAmountToMint);
    }
```

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
