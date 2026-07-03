### Title
Division by Zero in `getRsETHAmountToMint` When `rsETHPrice` Is Zero — (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool.getRsETHAmountToMint` unconditionally divides by `lrtOracle.rsETHPrice()` with no zero-value guard. The `rsETHPrice` storage variable in `LRTOracle` is **not set** in `initialize()` and therefore starts at `0`. If `updateRSETHPrice()` has not been called before the first deposit, every call to `depositAsset` and `depositETH` will revert with a division-by-zero panic, temporarily freezing all L1 deposits.

### Finding Description
`LRTOracle.initialize()` only stores the config address; it never writes `rsETHPrice`: [1](#0-0) 

`rsETHPrice` therefore remains `0` until `_updateRsETHPrice()` is first executed. That function sets `rsETHPrice = 1 ether` only when `rsethSupply == 0`: [2](#0-1) 

Otherwise it computes and stores `newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply)`, which equals `0` whenever `totalETHInProtocol == 0` with a non-zero supply: [3](#0-2) 

`LRTDepositPool.getRsETHAmountToMint` then divides by this stored value with no guard: [4](#0-3) 

`getRsETHAmountToMint` is called on every deposit path (`depositETH`, `depositAsset`) via `_beforeDeposit`. When `rsETHPrice == 0` the EVM division-by-zero panic fires and the entire transaction reverts. [5](#0-4) 

### Impact Explanation
All L1 deposits (`depositETH`, `depositAsset`) revert for as long as `rsETHPrice` remains zero. No user funds already in the protocol are lost, but new deposits are completely blocked — a **temporary freezing of funds** (Medium).

### Likelihood Explanation
Two realistic triggers exist:

1. **Deployment race**: `updateRSETHPrice()` is `public whenNotPaused` and can be called by anyone, but nothing in the deployment sequence enforces that it is called before the first deposit. On a fresh deployment or after a re-initialization, `rsETHPrice` is `0` until the first price update.

2. **Total-ETH collapse**: If `totalETHInProtocol` ever reaches `0` while `rsethSupply > 0` (e.g., all collateral assets are slashed or their oracle prices drop to zero), `_updateRsETHPrice()` stores `rsETHPrice = 0`, permanently blocking deposits until a manager manually triggers a price update.

Both paths are reachable without any privileged action by the attacker; the second path can be triggered by an unprivileged caller invoking `updateRSETHPrice()` at the right moment.

### Recommendation
Add an explicit zero-value check before the division in `getRsETHAmountToMint`, mirroring the guard already present in `RSETHPoolV3.viewSwapAssetToPremintedRsETH`:

```solidity
uint256 price = lrtOracle.rsETHPrice();
if (price == 0) revert InvalidPrice();
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / price;
```

Additionally, enforce in `initialize()` (or a post-deployment script) that `updateRSETHPrice()` is called before any deposit is accepted.

### Proof of Concept
1. Deploy `LRTOracle` and `LRTDepositPool` on a fresh chain; do **not** call `updateRSETHPrice()`.
2. `rsETHPrice` is `0` (uninitialized storage slot).
3. Call `LRTDepositPool.depositETH{value: 1 ether}(0, "")`.
4. Execution reaches `getRsETHAmountToMint` → `(1e18 * assetPrice) / 0` → EVM division-by-zero panic → revert.
5. All deposits are blocked until `updateRSETHPrice()` is called by any account. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTOracle.sol (L64-68)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTOracle.sol (L214-222)
```text
    function _updateRsETHPrice() internal {
        address rsETHTokenAddress = lrtConfig.rsETH();
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
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
