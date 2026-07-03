### Title
Fee-on-Transfer Token Deposit Mints Unbacked rsETH Due to Unvalidated Actual Received Amount — (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.depositAsset()` calculates the rsETH mint amount from the caller-supplied `depositAmount` parameter before the `safeTransferFrom` call, with no balance-before/balance-after check. If a supported asset charges a fee on transfer, the contract receives fewer tokens than `depositAmount`, yet mints rsETH for the full `depositAmount`. The same structural flaw exists in `RSETHPoolV3.deposit(address,uint256,string)`. The result is a cumulative, irreversible gap between rsETH supply and actual backing assets — protocol insolvency.

---

### Finding Description

**Root cause — `LRTDepositPool.depositAsset()`** [1](#0-0) 

```solidity
// LRTDepositPool.sol:110-117
uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

// interactions
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
_mintRsETH(rsethAmountToMint);
```

`rsethAmountToMint` is derived entirely from the caller-supplied `depositAmount` via `getRsETHAmountToMint(asset, depositAmount)`: [2](#0-1) 

The `safeTransferFrom` call then executes. If the token deducts a transfer fee, the contract receives `depositAmount − fee_amount`, but `_mintRsETH` mints rsETH for the full `depositAmount`. No balance snapshot is taken before or after the transfer to measure what was actually received.

**Same flaw in `RSETHPoolV3.deposit(address,uint256,string)`** [3](#0-2) 

```solidity
// RSETHPoolV3.sol:284-292
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

feeEarnedInToken[token] += fee;

wrsETH.mint(msg.sender, rsETHAmount);
```

`viewSwapRsETHAmountAndFee` uses the caller-supplied `amount`, not the actual post-transfer balance delta. wrsETH is freshly minted for the full `amount` even though the pool received less. [4](#0-3) 

**No guard at asset registration**

Neither `LRTConfig.addSupportedAsset` (L1) nor `RSETHPoolV3.addSupportedToken` (L2) checks whether the token charges a transfer fee: [5](#0-4) 

---

### Impact Explanation

Every deposit with a fee-on-transfer token mints rsETH (or wrsETH) backed by fewer underlying assets than the mint calculation assumed. The accounting invariant `rsETH_supply × rsETHPrice == Σ(asset_i × assetPrice_i)` is violated by `fee_amount × assetPrice` on every such deposit. The gap is:

- **Cumulative** — each deposit adds to the shortfall.
- **Irreversible** — no mechanism exists to claw back already-minted rsETH.
- **Protocol-wide** — `getTotalAssetDeposits` counts actual on-chain balances, so the oracle-computed `rsETHPrice` will drift downward, diluting all rsETH holders.

**Impact: Critical — Protocol insolvency.**

---

### Likelihood Explanation

USDT on Ethereum carries a dormant fee mechanism (up to 20 bps, currently zero) that the Tether owner can activate unilaterally. Other tokens with live transfer fees (PAXG, STA) exist. The protocol's `addSupportedToken` / `addSupportedAsset` paths impose no fee-on-transfer check, so any future addition of such a token — or activation of USDT's dormant fee — immediately triggers the invariant break for every subsequent deposit. The entry path is fully unprivileged: any depositor calling `depositAsset` or `deposit` is the trigger.

**Likelihood: Medium.**

---

### Recommendation

Use a balance-before / balance-after pattern to measure the actual received amount, and base the rsETH mint calculation on that value:

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

uint256 rsethAmountToMint = _beforeDeposit(asset, actualReceived, minRSETHAmountExpected);
_mintRsETH(rsethAmountToMint);
```

Apply the same fix to `RSETHPoolV3.deposit(address,uint256,string)` and `RSETHPool.deposit(address,uint256,string)`.

---

### Proof of Concept

1. Governance adds USDT as a supported asset on L1 (or any fee-on-transfer token on an L2 pool).
2. Tether activates its 20 bps fee (`basisPointsRate = 20`).
3. User calls `LRTDepositPool.depositAsset(usdt, 1_000e6, minRsETH, "")`.
4. `rsethAmountToMint` is calculated for **1 000 USDT** worth of rsETH.
5. `safeTransferFrom` executes; USDT deducts 0.2% → contract receives **998 USDT**.
6. `_mintRsETH(rsethAmountToMint)` mints rsETH for **1 000 USDT** worth.
7. `getTotalAssetDeposits(usdt)` returns 998 USDT; rsETH supply reflects 1 000 USDT.
8. The 2 USDT shortfall is permanent. Repeated across thousands of deposits, the cumulative gap makes rsETH undercollateralized, breaking the protocol's solvency invariant. [6](#0-5) [7](#0-6)

### Citations

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

**File:** contracts/pools/RSETHPoolV3.sol (L271-293)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
        limitDailyMint(amount, token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L315-335)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L541-555)
```text
    function addSupportedToken(address token, address oracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(oracle);

        if (supportedTokenOracle[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;

        emit AddSupportedToken(token);
    }
```
