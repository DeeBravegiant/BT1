### Title
Missing `updateRSETHPrice()` Before Deposit Allows Minting Excess rsETH at Stale Price — (`contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool.depositETH()` and `depositAsset()` mint rsETH using the stored `LRTOracle.rsETHPrice` state variable without first calling `updateRSETHPrice()`. When staking or MEV rewards have accrued since the last price update, `rsETHPrice` is stale (lower than actual), and depositors receive more rsETH than they are entitled to. This dilutes existing holders and constitutes theft of their unclaimed yield.

---

### Finding Description

`LRTOracle` maintains a stored state variable `rsETHPrice` that is only updated when `updateRSETHPrice()` (or `updateRSETHPriceAsManager()`) is explicitly called. [1](#0-0) 

The `_updateRsETHPrice()` internal function computes the new price from the current total ETH in the protocol divided by the current rsETH supply, takes a protocol fee on any TVL increase, mints fee rsETH to the treasury, and writes the new price to `rsETHPrice`. [2](#0-1) 

`LRTDepositPool.depositETH()` and `depositAsset()` both call `_beforeDeposit()`, which calls `getRsETHAmountToMint()`: [3](#0-2) [4](#0-3) 

The minting formula is:

```
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.rsETHPrice()` returns the **stored** state variable — not a freshly computed value. Neither `depositETH()` nor `depositAsset()` calls `updateRSETHPrice()` before minting. A grep across all production contracts confirms `updateRSETHPrice` is defined and called only within `LRTOracle.sol` itself; it is never invoked from `LRTDepositPool`.

Rewards flow into the protocol via `FeeReceiver.sendFunds()`, which transfers accumulated MEV/execution-layer rewards directly to `LRTDepositPool`: [5](#0-4) 

After `sendFunds()` executes, `totalETHInProtocol` increases immediately (because `getETHDistributionData()` reads `address(this).balance` of the deposit pool), but `rsETHPrice` remains at its last written value until `updateRSETHPrice()` is called. Any deposit that occurs in this window uses the stale, lower price.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

When `rsETHPrice` is stale (lower than actual), a depositor of `D` ETH receives `D / P_stale` rsETH instead of the correct `D / P_actual`. Because `P_stale < P_actual`, the depositor receives excess rsETH. This excess represents a claim on protocol TVL that was earned by existing holders as staking/MEV yield, not by the new depositor.

**Numerical example** (10% protocol fee, 10% reward accrual):

| Step | TVL | rsETH Supply | rsETHPrice |
|---|---|---|---|
| Initial | 1000 ETH | 1000 | 1.000 |
| After 100 ETH rewards (price not updated) | 1100 ETH | 1000 | 1.000 (stale) |
| Depositor puts in 100 ETH at stale price | 1200 ETH | 1100 | 1.000 (stale) |
| After `updateRSETHPrice()` | 1200 ETH | ~1109.3 | ~1.0727 |

- Existing 1000 rsETH holders receive: `1000 × 1.0727 = 1072.7 ETH`
- If `updateRSETHPrice()` had been called first, they would receive: `1000 × 1.08 = 1080 ETH`
- **Loss to existing holders: ~7.3 ETH** (captured by the depositor as excess rsETH)

---

### Likelihood Explanation

`updateRSETHPrice()` is a permissionless public function with no on-chain enforcement that it be called before every deposit. Rewards accumulate continuously via staking and MEV. Any deposit made between a reward accrual event (e.g., `FeeReceiver.sendFunds()`) and the next `updateRSETHPrice()` call exploits the stale price. This window is routine and requires no special attacker capability — any ordinary depositor benefits passively, and a sophisticated actor can deliberately time deposits to maximize the dilution.

---

### Recommendation

Call `updateRSETHPrice()` (or an equivalent internal price-settlement hook) at the start of `depositETH()` and `depositAsset()` in `LRTDepositPool`, before computing `rsethAmountToMint`:

```solidity
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId)
    external payable nonReentrant whenNotPaused onlySupportedAsset(LRTConstants.ETH_TOKEN)
{
    ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice(); // <-- add
    uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);
    emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
}
```

Apply the same fix to `depositAsset()`. This mirrors the Sentiment fix of calling `beforeDeposit()` (which internally calls `updateState()`) before any liquidity change.

---

### Proof of Concept

1. Protocol has 1000 ETH TVL, 1000 rsETH supply, `rsETHPrice = 1.0`.
2. `FeeReceiver.sendFunds()` is called, transferring 100 ETH of MEV rewards to `LRTDepositPool`. TVL is now 1100 ETH. `rsETHPrice` remains `1.0` (stale).
3. Alice calls `depositETH{value: 100 ETH}(0, "")`. `getRsETHAmountToMint` computes `100e18 * 1e18 / 1e18 = 100e18` rsETH. Alice receives 100 rsETH. TVL = 1200 ETH, supply = 1100 rsETH.
4. Anyone calls `updateRSETHPrice()`. `previousTVL = 1100 * 1.0 = 1100`. `rewardAmount = 1200 - 1100 = 100`. Protocol fee (10%) = 10 ETH. `newRsETHPrice ≈ 1.0727`. Fee rsETH minted to treasury ≈ 9.32 rsETH.
5. Alice's 100 rsETH is worth `100 × 1.0727 = 107.27 ETH` — she deposited 100 ETH and immediately captured ~7.27 ETH of yield that belonged to the original 1000 rsETH holders.
6. The original holders' 1000 rsETH is worth `1072.7 ETH` instead of the correct `1080 ETH` — a loss of ~7.3 ETH of unclaimed yield. [3](#0-2) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

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

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```
