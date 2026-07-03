### Title
Permissionless `FeeReceiver.sendFunds()` Enables Front-Running to Steal Yield from rsETH Holders — (File: `contracts/FeeReceiver.sol`)

---

### Summary

`FeeReceiver.sendFunds()` carries no access-control modifier, so any external caller can trigger the transfer of accumulated MEV/execution-layer rewards into `LRTDepositPool` at a time of their choosing. Because `LRTOracle.updateRSETHPrice()` is also permissionless, an attacker can atomically (1) deposit a large amount of ETH to inflate their rsETH share, (2) flush the pending rewards into the pool, and (3) update the oracle price — capturing a disproportionate fraction of the yield that should accrue to pre-existing rsETH holders.

---

### Finding Description

`FeeReceiver.sendFunds()` is declared with no role modifier: [1](#0-0) 

It calls `LRTDepositPool.receiveFromRewardReceiver()`, which is also unrestricted: [2](#0-1) 

When ETH lands in the deposit pool, `getETHDistributionData()` immediately counts it as part of the protocol TVL via `address(this).balance`: [3](#0-2) 

`LRTOracle.updateRSETHPrice()` is `public whenNotPaused` — no role required — and recomputes the rsETH price as `totalETHInProtocol / rsethSupply`: [4](#0-3) [5](#0-4) 

`LRTDepositPool.depositETH()` mints rsETH at the **currently stored** oracle price, not a freshly computed one: [6](#0-5) 

Because the stored price is stale until `updateRSETHPrice()` is called, an attacker who deposits *before* flushing the FeeReceiver receives rsETH at the pre-reward price, then forces the price update, leaving them holding rsETH that is worth more than what they paid — at the expense of existing holders.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Let existing TVL = T, rsETH supply = S (price = T/S), and pending rewards in FeeReceiver = R.

- Attacker deposits A ETH → receives `A·S/T` rsETH at price T/S.
- Attacker calls `FeeReceiver.sendFunds()` → pool TVL becomes T + A + R.
- Attacker calls `updateRSETHPrice()` → new price = `(T+A+R)·T / (S·(T+A))`.
- Attacker's rsETH is now worth `A + A·R/(T+A)` ETH.
- **Attacker profit = `A·R/(T+A)`** — a direct transfer of yield from pre-existing holders.

Pre-existing holders receive only `R·T/(T+A)` of the R ETH reward instead of the full R ETH. The larger A is relative to T, the greater the theft. With a flash loan amplifying A, the attacker can capture nearly the entire reward in a single block.

---

### Likelihood Explanation

**High.** Both `FeeReceiver.sendFunds()` and `LRTOracle.updateRSETHPrice()` are callable by any EOA or contract with no preconditions. MEV searchers routinely monitor on-chain balances; the FeeReceiver accumulates ETH continuously from validator tips and MEV. The attack is profitable whenever `R` (pending rewards) is non-trivial relative to gas cost, which is true after any meaningful reward accumulation period. No privileged role, leaked key, or governance action is required.

---

### Recommendation

1. **Restrict `FeeReceiver.sendFunds()`** to a trusted role (e.g., `LRTConstants.MANAGER` or a dedicated `KEEPER_ROLE`) so that the timing of reward distribution cannot be controlled by an adversary.

```solidity
function sendFunds() external onlyRole(LRTConstants.MANAGER) {
    ...
}
```

2. **Alternatively**, snapshot the pending reward amount at deposit time and exclude it from the minting price calculation until a permissioned price update is performed — preventing a depositor from benefiting from rewards that were already accruing before their deposit.

3. As a defence-in-depth measure, consider adding a minimum holding period before newly minted rsETH can be used to initiate a withdrawal, analogous to the `_updateCallerBlock` / `_checkSameTx` pattern recommended in the reference report.

---

### Proof of Concept

```
Block N:
  FeeReceiver holds 50 ETH of accumulated MEV rewards.
  Protocol TVL = 1 000 ETH, rsETH supply = 1 000, price = 1.00 ETH/rsETH.

Attacker's atomic transaction (or across two blocks if instant-withdrawal is off):
  1. Flash-loan 1 000 ETH.
  2. Call LRTDepositPool.depositETH{value: 1000 ETH}()
       → minted 1 000 rsETH at stored price 1.00.
       → TVL = 2 000 ETH, supply = 2 000, stored price still 1.00.
  3. Call FeeReceiver.sendFunds()          ← permissionless
       → 50 ETH moves to deposit pool.
       → TVL = 2 050 ETH, supply = 2 000.
  4. Call LRTOracle.updateRSETHPrice()    ← permissionless
       → new price = 2 050 / 2 000 = 1.025 ETH/rsETH.
  5. Attacker holds 1 000 rsETH worth 1 025 ETH.
     Repay flash loan (1 000 ETH) → net profit = 25 ETH.

Pre-existing holders: their 1 000 rsETH is worth 1 025 ETH instead of 1 050 ETH.
Yield stolen from them: 25 ETH (half the total reward R = 50 ETH).
```

Entry path: `FeeReceiver.sendFunds()` (no modifier) → `LRTDepositPool.receiveFromRewardReceiver()` (no modifier) → `LRTOracle.updateRSETHPrice()` (public). All steps are reachable by any unprivileged external caller. [1](#0-0) [4](#0-3) [7](#0-6)

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTDepositPool.sol (L61-61)
```text
    function receiveFromRewardReceiver() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L76-92)
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
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```
