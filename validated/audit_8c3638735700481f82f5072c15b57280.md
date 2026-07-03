### Title
Late rsETH Depositors Steal Accumulated MEV/Reward Yield from Existing Holders via Stale Price Window - (File: contracts/FeeReceiver.sol, contracts/LRTOracle.sol)

---

### Summary

`FeeReceiver.sendFunds()` is publicly callable with no access control, and `LRTOracle.updateRSETHPrice()` is a public function with no role restriction. When MEV/execution-layer rewards accumulate in `FeeReceiver` and are flushed into `LRTDepositPool`, the stored `rsETHPrice` is not updated atomically. A depositor who enters between the reward flush and the price update receives rsETH at the stale (lower) price, effectively stealing yield that belongs to existing rsETH holders.

---

### Finding Description

`LRTOracle` stores `rsETHPrice` as a state variable updated only when `updateRSETHPrice()` is explicitly called. [1](#0-0)  The price update function is unrestricted — any caller may invoke it. [2](#0-1) 

`FeeReceiver.sendFunds()` is also unrestricted — any external caller can flush the entire accumulated ETH balance into `LRTDepositPool`. [3](#0-2) 

`LRTOracle._getTotalEthInProtocol()` reads the deposit pool's live asset balance via `getTotalAssetDeposits`, so the moment `sendFunds()` executes, the true TVL rises above `rsethSupply × rsETHPrice`. [4](#0-3) 

However, `rsETHPrice` is not updated until `updateRSETHPrice()` is separately called. [5](#0-4)  During this window, new depositors call `LRTDepositPool.depositETH()` or `depositAsset()`, which mints rsETH using the stale (lower) price. [6](#0-5)  They receive more rsETH than the true post-reward price would allow. When `updateRSETHPrice()` is subsequently called, the new price is computed as `(totalETH − fee) / totalSupply`, where `totalSupply` now includes the attacker's inflated rsETH balance, diluting the price increase that existing holders were entitled to. [7](#0-6) 

---

### Impact Explanation

Existing rsETH holders suffer theft of unclaimed yield. MEV and execution-layer rewards that accrued while they held rsETH are partially redirected to a late depositor who contributed nothing to earning those rewards. The attacker's profit equals the fraction of the reward they capture by diluting the supply before the price update. This maps directly to **High — Theft of unclaimed yield**.

---

### Likelihood Explanation

- `FeeReceiver.sendFunds()` has no access control; any EOA or contract can call it. [3](#0-2) 
- `LRTOracle.updateRSETHPrice()` has no access control. [2](#0-1) 
- MEV rewards accumulate continuously in `FeeReceiver` and are not flushed atomically with price updates.
- The attacker controls the exact ordering: deposit → `sendFunds()` → `updateRSETHPrice()`, all in a single transaction or across two blocks.
- No privileged role, oracle compromise, or governance capture is required.

Likelihood is **High**.

---

### Recommendation

Atomically update `rsETHPrice` inside `FeeReceiver.sendFunds()` immediately after transferring funds to the deposit pool, so the price reflects the new TVL before any subsequent deposit can be minted at the stale rate. Alternatively, restrict `sendFunds()` to a privileged role and enforce that `updateRSETHPrice()` is called in the same transaction as the reward flush. A cumulative reward-per-share checkpoint (analogous to `rewardPerTokenStored` in `KernelDepositPool`) recorded at deposit time would also prevent late entrants from claiming rewards that accrued before their entry. [8](#0-7) 

---

### Proof of Concept

1. MEV rewards of `R` ETH accumulate in `FeeReceiver`. Current `rsETHPrice = P`, `totalSupply = S`, so TVL = `S × P`.
2. Attacker calls `LRTDepositPool.depositETH{value: D}(...)`. rsETH minted = `D / P`. Attacker holds `D/P` rsETH.
3. Attacker calls `FeeReceiver.sendFunds()`. Deposit pool ETH balance increases by `R`. True TVL is now `S×P + D + R`.
4. Attacker calls `LRTOracle.updateRSETHPrice()`. New price = `(S×P + D + R) / (S + D/P)`. [7](#0-6) 
5. Attacker's rsETH is now worth `(D/P) × newPrice > D`. The excess comes from `R`, which was earned by existing holders before the attacker deposited.
6. Existing holders' share of `R` is diluted by `D/P / (S + D/P)`, which is the attacker's fraction of the new total supply.

Steps 2–4 can be executed atomically in a single transaction, requiring no flash loan. The only constraint is the withdrawal delay in `LRTWithdrawalManager`, which delays but does not prevent profit extraction. [3](#0-2)

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

**File:** contracts/LRTOracle.sol (L244-250)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTOracle.sol (L331-348)
```text
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
```

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L81-84)
```text
    uint256 public rewardPerTokenStored;

    /// @notice The latest rewardPerTokenStored checkpoint for each account. It gets updated on each user action
    mapping(address user => uint256 rewardPerTokenPaid) public userRewardPerTokenPaid;
```
