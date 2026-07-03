### Title
Unprotected `sendFunds()` in `FeeReceiver` Allows Anyone to Force MEV Reward Distribution and Front-Run rsETH Price Increase — (File: `contracts/FeeReceiver.sol`)

---

### Summary

`FeeReceiver.sendFunds()` has no access control, allowing any external caller to force all accumulated MEV/execution-layer rewards to be transferred from `FeeReceiver` into `LRTDepositPool` at an arbitrary time. An attacker can exploit this to front-run the reward distribution: deposit ETH to receive rsETH at the pre-reward price, trigger `sendFunds()` to inflate the rsETH price, and exit at a profit — stealing yield from existing rsETH holders.

---

### Finding Description

`FeeReceiver` is the designated recipient of MEV and execution-layer rewards for the Kelp DAO protocol. Its ETH balance is intentionally **excluded** from the rsETH TVL calculation until explicitly flushed into `LRTDepositPool`. The comment in `LRTDepositPool.getETHDistributionData()` confirms this design:

> *"rewards are not accounted here / it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool"*

The function responsible for flushing rewards is `sendFunds()`:

```solidity
// contracts/FeeReceiver.sol:53-58
function sendFunds() external {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

There is **no access control modifier** on this function. Every other state-changing function in the same contract that moves configuration (`setDepositPool`) requires `onlyRole(LRTConstants.MANAGER)`, but `sendFunds()` is callable by any EOA or contract.

`LRTDepositPool.receiveFromRewardReceiver()` is equally unguarded:

```solidity
// contracts/LRTDepositPool.sol:61
function receiveFromRewardReceiver() external payable { }
```

When `sendFunds()` is called, the ETH moves from `FeeReceiver` (not counted in TVL) into `LRTDepositPool` (counted as `address(this).balance` in `getETHDistributionData()`). This increases `totalETHInProtocol` in `LRTOracle._getTotalEthInProtocol()`, which raises the rsETH price on the next `updateRSETHPrice()` call — a public, permissionless function.

---

### Impact Explanation

**Impact: High — Theft of unclaimed yield.**

Let the current protocol TVL be `V` ETH, rsETH supply `N`, and accumulated FeeReceiver balance `R` ETH (MEV rewards not yet counted in TVL).

1. Attacker deposits `D` ETH → receives `D·N/V` rsETH at price `V/N`.
2. Attacker calls `sendFunds()` → `R` ETH enters the deposit pool.
3. New TVL = `V + D + R`, new rsETH supply = `N + D·N/V`.
4. New rsETH price = `(V + D + R)·V / (N·(V + D))`.
5. Attacker's rsETH is worth `D·(V + D + R)/(V + D)` ETH.
6. **Attacker profit = `D·R/(V+D)` ETH**, extracted directly from existing holders' share of MEV rewards.
7. Existing holders lose exactly `D·R/(V+D)` ETH of yield they had earned.

The attack is amplified by large `D` (attacker deposit) or large `R` (accumulated MEV rewards). The attacker can repeat this every time rewards accumulate.

---

### Likelihood Explanation

**Likelihood: High.**

- No privileged access is required — any EOA can call `sendFunds()`.
- The FeeReceiver balance is publicly observable on-chain; an attacker can monitor it and act when `R` is large.
- `updateRSETHPrice()` is also public, so the attacker controls the full sequence in a single transaction or block.
- The only partial mitigation is `pricePercentageLimit` in `LRTOracle`, which reverts non-manager callers if the price increase exceeds the configured threshold. However, if `R` is small relative to `V`, the increase stays within the limit and the check passes.

---

### Recommendation

Add an access control modifier to `sendFunds()` restricting it to the `MANAGER` role, consistent with every other privileged function in the contract:

```solidity
function sendFunds() external onlyRole(LRTConstants.MANAGER) {
    uint256 balance = address(this).balance;
    ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();
    emit MevRewardsAddedToTVL(balance);
}
```

Similarly, consider adding a caller check to `receiveFromRewardReceiver()` in `LRTDepositPool` to ensure only the registered `FeeReceiver` contract can invoke it.

---

### Proof of Concept

**Setup:** Protocol TVL = 1000 ETH, rsETH supply = 1000 rsETH (price = 1 ETH/rsETH). FeeReceiver holds 50 ETH in accumulated MEV rewards.

**Step 1 — Attacker deposits:**
```
LRTDepositPool.depositETH{value: 1000 ETH}(minRsETH, "")
// Attacker receives 1000 rsETH at price 1.0 ETH/rsETH
// TVL = 2000 ETH, supply = 2000 rsETH
```

**Step 2 — Attacker forces reward flush:**
```
FeeReceiver.sendFunds()
// 50 ETH moves from FeeReceiver → LRTDepositPool
// TVL = 2050 ETH, supply = 2000 rsETH
```

**Step 3 — Attacker triggers price update:**
```
LRTOracle.updateRSETHPrice()
// New rsETH price = 2050/2000 = 1.025 ETH/rsETH
```

**Step 4 — Attacker exits:**
```
// Attacker's 1000 rsETH is now worth 1025 ETH
// Attacker profit = 25 ETH
// Existing 1000 rsETH holders receive 1025 ETH instead of 1050 ETH
// Existing holders' loss = 25 ETH (stolen yield)
```

The attacker extracted 25 ETH of MEV yield that belonged to the original 1000 rsETH holders, with no privileged access required. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/FeeReceiver.sol (L64-72)
```text
    /// @dev Set the deposit pool
    /// @param _depositPool Address of the deposit pool
    function setDepositPool(address _depositPool) external onlyRole(LRTConstants.MANAGER) {
        if (_depositPool == address(0)) revert InvalidEmptyValue();

        depositPool = _depositPool;

        emit DepositPoolSet(_depositPool);
    }
```

**File:** contracts/LRTDepositPool.sol (L60-67)
```text
    /// @dev receive from RewardReceiver
    function receiveFromRewardReceiver() external payable { }

    /// @dev receive from LRTConverter
    function receiveFromLRTConverter() external payable { }

    /// @dev receive from NodeDelegator
    function receiveFromNodeDelegator() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L464-500)
```text
    /// @dev provides ETH amount distribution data among depositPool, NDCs and eigenLayer
    /// @dev rewards are not accounted here
    /// it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool
    function getETHDistributionData()
        public
        view
        override
        returns (
            uint256 ethLyingInDepositPool,
            uint256 ethLyingInNDCs,
            uint256 ethStakedInEigenLayer,
            uint256 ethUnstakingFromEigenLayer,
            uint256 ethLyingInConverter,
            uint256 ethLyingInUnstakingVault
        )
    {
        ethLyingInDepositPool = address(this).balance;

        uint256 ndcsCount = nodeDelegatorQueue.length;

        for (uint256 i; i < ndcsCount;) {
            ethLyingInNDCs += nodeDelegatorQueue[i].balance;

            ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
            ethUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i])
                .getAssetUnstaking(LRTConstants.ETH_TOKEN);
            unchecked {
                ++i;
            }
        }

        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);
        ethLyingInUnstakingVault = lrtUnstakingVault.balance;

        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
    }
```

**File:** contracts/LRTOracle.sol (L214-250)
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

**File:** contracts/LRTOracle.sol (L331-349)
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
    }
```
