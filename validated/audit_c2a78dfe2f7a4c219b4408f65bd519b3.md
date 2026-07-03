### Title
Permissionless `FeeReceiver.sendFunds()` + `LRTOracle.updateRSETHPrice()` Allows Depositors to Sandwich Reward Distribution and Steal Yield from Existing rsETH Holders - (File: `contracts/FeeReceiver.sol`, `contracts/LRTOracle.sol`)

---

### Summary

`FeeReceiver.sendFunds()` carries no access control and `LRTOracle.updateRSETHPrice()` is a public function. MEV/execution-layer rewards accumulate in `FeeReceiver` between calls, leaving the on-chain rsETH price stale (lower than fair value). An unprivileged depositor can atomically: (1) deposit ETH at the stale price to receive more rsETH than fair value, (2) call `sendFunds()` to push the pending rewards into `LRTDepositPool`, and (3) call `updateRSETHPrice()` to crystallise the price increase. The attacker's rsETH is immediately worth more than they paid; existing holders receive proportionally less of the reward than they are entitled to.

---

### Finding Description

**Step 1 – Reward accumulation creates a stale price window.**

`FeeReceiver` receives MEV and execution-layer rewards via its `receive()` fallback. These funds sit in the contract and are not reflected in `rsETHPrice` until `sendFunds()` is called and `updateRSETHPrice()` is subsequently executed. [1](#0-0) 

**Step 2 – Anyone can flush rewards into the deposit pool.**

`sendFunds()` has no `onlyRole` or similar modifier. Any EOA or contract can call it at will. [2](#0-1) 

**Step 3 – Anyone can trigger the price update.**

`updateRSETHPrice()` is `public` with only a `whenNotPaused` guard. [3](#0-2) 

**Step 4 – Price is computed as `(totalETH − fee) / rsETHSupply`.**

`previousTVL` is anchored to the *current* rsETH supply multiplied by the *stale* `rsETHPrice`. A deposit made before the price update inflates the supply denominator while the numerator (total ETH) only grows by the deposited amount, not by the pending rewards. After `sendFunds()` the pending rewards are added to the numerator, raising the price for all holders including the late depositor. [4](#0-3) 

**Step 5 – No deposit lockup exists.**

`LRTDepositPool.depositETH()` mints rsETH immediately with no time-lock. The attacker can deposit, trigger the price update in the same block or the next, and then queue a withdrawal at the inflated price. [5](#0-4) 

---

### Impact Explanation

**Impact: High – Theft of unclaimed yield.**

Concrete numerical example (ignoring protocol fee for clarity):

| State | ETH in protocol | rsETH supply | Fair price |
|---|---|---|---|
| Before attack | 100 | 100 | 1.000 |
| Attacker deposits 10 ETH at stale price | 110 | 110 | 1.000 (stale) |
| After `sendFunds()` (10 ETH rewards) | 120 | 110 | 1.0909 |

- Attacker's 10 rsETH is now worth **10.909 ETH** → profit **0.909 ETH**.
- Existing 100 rsETH holders receive **108.18 ETH** instead of the **109 ETH** they would have received without the attacker.
- The attacker extracted ~9% of the reward that belonged to existing holders.

The profit scales linearly with the size of the pending reward balance in `FeeReceiver`. For large reward accumulations (e.g., after a period of high MEV), the stolen yield can be material.

---

### Likelihood Explanation

**Likelihood: High.**

- Both trigger functions (`sendFunds`, `updateRSETHPrice`) are permissionless and callable by any EOA.
- The pending reward balance in `FeeReceiver` is publicly visible on-chain; a bot can monitor it and execute the sandwich atomically.
- No special knowledge, privileged access, or external oracle manipulation is required.
- The 8-day withdrawal delay (`withdrawalDelayBlocks`) does not prevent the attack; the attacker simply holds rsETH for 8 days and then claims the inflated amount. The capital is at risk for 8 days but the profit is locked in at deposit time. [6](#0-5) 

---

### Recommendation

1. **Add access control to `FeeReceiver.sendFunds()`** – restrict it to `MANAGER` or `OPERATOR` role so that reward flushing cannot be triggered permissionlessly.
2. **Atomically flush and update price** – have the authorised caller flush rewards and call `updateRSETHPrice()` in the same transaction, eliminating the window between the two.
3. **Alternatively, implement a deposit lockup** – prevent newly minted rsETH from being used in withdrawal requests for at least one price-update cycle, analogous to the epoch-based buffer recommended in the reference report.

---

### Proof of Concept

```
State:
  LRTDepositPool: 100 ETH
  rsETH supply:   100
  rsETHPrice:     1.0 ETH  (stale – 10 ETH rewards sitting in FeeReceiver)

Tx 1 – Attacker calls LRTDepositPool.depositETH{value: 10 ether}(0, "")
  rsethAmountToMint = 10e18 * 1e18 / 1e18 = 10e18   ← uses stale price
  Attacker receives 10 rsETH
  Pool: 110 ETH, supply: 110 rsETH

Tx 2 – Attacker calls FeeReceiver.sendFunds()
  10 ETH rewards transferred to LRTDepositPool
  Pool: 120 ETH, supply: 110 rsETH

Tx 3 – Attacker calls LRTOracle.updateRSETHPrice()
  previousTVL = 110 * 1.0 = 110 ETH
  totalETHInProtocol = 120 ETH
  rewardAmount = 10 ETH  →  protocolFee = 10 * fee%
  newRsETHPrice = (120 - fee) / 110  ≈  1.0818 ETH/rsETH  (at 10% fee)

After 8 days – Attacker calls initiateWithdrawal + completeWithdrawal
  Attacker redeems 10 rsETH at 1.0818 → receives 10.818 ETH
  Net profit: 0.818 ETH stolen from existing holders
  Existing 100 rsETH holders: 108.18 ETH instead of 109 ETH
```

The attack requires no flash loan, no oracle manipulation, and no privileged role. It is executable by any on-chain bot monitoring the `FeeReceiver` balance.

### Citations

**File:** contracts/FeeReceiver.sol (L49-58)
```text
    /// @dev fallback to receive funds
    receive() external payable { }

    /// @dev send all rewards to deposit pool
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L228-250)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```
