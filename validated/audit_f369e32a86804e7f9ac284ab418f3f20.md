### Title
Stale `rsETHPrice` Used in Deposit Minting Without Forcing `updateRSETHPrice()` - (File: `contracts/LRTDepositPool.sol`)

---

### Summary

`LRTDepositPool.depositETH()` and `depositAsset()` compute the rsETH amount to mint using the cached `LRTOracle.rsETHPrice` storage variable without first calling `updateRSETHPrice()`. Because `rsETHPrice` is only updated when that function is explicitly invoked, any rewards that have accrued since the last update cause the stored price to be lower than the true current value. Depositors who deposit while the price is stale receive more rsETH than they are entitled to, extracting accrued yield from existing rsETH holders.

---

### Finding Description

`LRTOracle` stores the rsETH/ETH exchange rate in the state variable `rsETHPrice`. This value is only updated when `updateRSETHPrice()` (public, callable by anyone) or `updateRSETHPriceAsManager()` is explicitly called. The update computes the true current price from live TVL data:

```
newRsETHPrice = (totalETHInProtocol - protocolFeeInETH) / rsethSupply
``` [1](#0-0) 

The deposit path in `LRTDepositPool` never triggers this update. Both `depositETH()` and `depositAsset()` call `_beforeDeposit()`, which calls `getRsETHAmountToMint()`:

```
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

`lrtOracle.rsETHPrice()` returns the **cached** storage value, not a freshly computed one. [3](#0-2) 

`_beforeDeposit` is declared `private view`, so it cannot call any state-mutating function like `updateRSETHPrice()`. Neither `depositETH()` nor `depositAsset()` calls `updateRSETHPrice()` before invoking `_beforeDeposit`. [4](#0-3) [5](#0-4) 

As EigenLayer restaking rewards and LST yield accrue over time, the true rsETH/ETH rate rises above the stored `rsETHPrice`. The formula `rsethAmountToMint = amount * assetPrice / staleLowRsETHPrice` then mints **more** rsETH than the depositor's contribution warrants. The excess rsETH represents a claim on TVL that was earned by existing holders before the deposit.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Every deposit made while `rsETHPrice` is stale dilutes existing rsETH holders by minting excess rsETH against TVL they earned. The magnitude scales with (a) the time elapsed since the last price update and (b) the deposit size. Because `updateRSETHPrice()` is never called atomically within the deposit flow, this condition is persistent and not an edge case — it applies to every deposit in every block where the price has not been refreshed.

---

### Likelihood Explanation

**High.** `updateRSETHPrice()` is a separate, permissionless transaction. There is no on-chain enforcement that it must be called before a deposit. In normal operation, price updates are infrequent (off-chain keeper or manual manager calls). Any depositor — including a bot watching the mempool — can deposit immediately after rewards accrue and before the price is refreshed, systematically extracting yield from existing holders.

---

### Recommendation

Call `updateRSETHPrice()` (or an equivalent internal `_updateRsETHPrice()`) at the beginning of `depositETH()` and `depositAsset()`, before the rsETH amount is computed. This mirrors the fix applied in the Reserve Protocol M-07 mitigation: refresh the exchange rate before any operation that depends on it.

```solidity
function depositETH(...) external payable nonReentrant whenNotPaused ... {
    ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE)).updateRSETHPrice(); // add this
    uint256 rsethAmountToMint = _beforeDeposit(...);
    _mintRsETH(rsethAmountToMint);
    ...
}
```

Alternatively, refactor `_beforeDeposit` from `private view` to `private` and call `_updateRsETHPrice()` internally.

---

### Proof of Concept

1. At time T₀, `updateRSETHPrice()` is called. Stored `rsETHPrice = 1.04e18` (1.04 ETH per rsETH).
2. EigenLayer rewards accrue. True rsETH price rises to `1.05e18`, but `rsETHPrice` in storage remains `1.04e18`.
3. Attacker calls `depositETH{value: 10 ether}(0, "")`.
4. `getRsETHAmountToMint` computes: `10e18 * 1e18 / 1.04e18 ≈ 9.615 rsETH`.
5. True fair amount at current price: `10e18 * 1e18 / 1.05e18 ≈ 9.524 rsETH`.
6. Attacker receives **~0.091 excess rsETH**, representing a claim on ~0.095 ETH of TVL earned by existing holders.
7. Existing rsETH holders' share of TVL is diluted by this amount.

The attack requires no special privileges — any unprivileged depositor triggers it on every deposit where `rsETHPrice` has not been refreshed in the same block. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L249-250)
```text
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
