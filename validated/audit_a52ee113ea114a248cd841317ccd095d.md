### Title
Linea Bridge Fee (`minimumFee`) Silently Reduces Bridged ETH, Causing rsETH Undercollateralization — (File: contracts/bridges/LineaMessenger.sol)

---

### Summary

`LineaMessenger.sendETHToL1ViaBridge` deducts the Linea native bridge fee (`minimumFee`) from the ETH being bridged to L1, but this cost is never accounted for in the rsETH minting calculation on L2. Every bridge cycle causes the L1 vault to receive less ETH than the amount used to mint rsETH, progressively undercollateralizing the token.

---

### Finding Description

When a pool (e.g., `RSETHPoolV2`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`) calls `bridgeAssetsViaNativeBridge()`, it passes `ethBalanceMinusFees` — the full ETH balance minus the protocol's `feeBps` cut — to `LineaMessenger.sendETHToL1ViaBridge`:

```solidity
// RSETHPoolV2.sol (and identical pattern in V3 variants)
uint256 ethBalanceMinusFees = getETHBalanceMinusFees();
IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
    l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
);
```

Inside `LineaMessenger`, the Linea bridge's `minimumFeeInWei()` is fetched at call time and passed as the fee argument to `sendMessage`. The bridge deducts this fee from the forwarded ETH before crediting the L1 target:

```solidity
// LineaMessenger.sol
uint256 minimumFee = ILineaMessageService(l2bridge).minimumFeeInWei();
if (value <= minimumFee) revert InsufficientAmountForBridge();
ILineaMessageService(l2bridge).sendMessage{ value: value }(target, minimumFee, bytes(""));
```

The L1 vault therefore receives `ethBalanceMinusFees − minimumFee`, not `ethBalanceMinusFees`.

Meanwhile, rsETH was already minted to depositors based on the full `amount − feeBps` ETH. The `feeBps` is a fixed protocol fee that is entirely independent of the bridge fee:

```solidity
// RSETHPoolV2.sol (and all pool variants)
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

No deduction for `minimumFee` is made here or anywhere in the deposit path. The bridge fee is silently absorbed from the pool's ETH balance, reducing the ETH that backs the already-minted rsETH.

This is structurally identical to the xkeeper finding: a hard-coded fee formula accounts for only one cost component (protocol `feeBps`) while ignoring a chain-specific cost (`minimumFeeInWei`) that is deducted at bridge time.

---

### Impact Explanation

Every Linea → L1 bridge cycle leaves the L1 vault short by `minimumFee` wei relative to the rsETH supply minted on L2. Over many cycles, or if `minimumFeeInWei()` is elevated (Linea's minimum fee is dynamic and can be governance-adjusted), the cumulative shortfall grows. rsETH holders redeeming on L1 receive slightly less ETH than the rate they were quoted at deposit time.

**Impact class**: Low — Contract fails to deliver promised returns; rsETH is backed by less ETH than the minting rate implied.

---

### Likelihood Explanation

This triggers on every invocation of `bridgeAssetsViaNativeBridge()` on any pool deployed on Linea that uses `LineaMessenger`. It requires no attacker — it is a structural accounting gap that fires automatically on every bridge operation performed by the `BRIDGER_ROLE`.

---

### Recommendation

The Linea bridge fee must be excluded from the ETH that was used to mint rsETH. Two sound approaches:

1. **Caller-supplied fee**: Require the `BRIDGER_ROLE` to supply `minimumFee` as additional `msg.value` on top of the bridged amount, so the pool's depositor ETH is not consumed by the bridge fee.
2. **Pre-deduction at mint time**: Query `minimumFeeInWei()` (or a conservative upper bound) and subtract it from the deposit amount before computing `rsETHAmount`, analogous to how Gelato explicitly accounts for L1 data fees in its fee oracle.

---

### Proof of Concept

1. Alice deposits 1 ETH into the Linea `RSETHPoolV2`. `feeBps = 10` (0.1 %).
2. `viewSwapRsETHAmountAndFee(1e18)` → `fee = 1e15`, `amountAfterFee = 0.999 ETH`. rsETH minted to Alice at the current rate.
3. `feeEarnedInETH += 1e15`; pool holds `0.999 ETH` of depositor ETH.
4. BRIDGER calls `bridgeAssetsViaNativeBridge()`. `getETHBalanceMinusFees()` returns `0.999 ETH`.
5. `LineaMessenger` fetches `minimumFee = 5e14` (0.0005 ETH, a realistic Linea value).
6. `sendMessage{ value: 0.999 ETH }(l1Vault, 5e14, "")` — Linea bridge credits L1 vault with `0.999 ETH − 0.0005 ETH = 0.9985 ETH`.
7. Alice's rsETH was minted against `0.999 ETH`; only `0.9985 ETH` backs it. The `0.0005 ETH` gap is unaccounted for and accumulates with every bridge cycle. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/bridges/LineaMessenger.sol (L39-43)
```text
        uint256 minimumFee = ILineaMessageService(l2bridge).minimumFeeInWei();
        if (value <= minimumFee) revert InsufficientAmountForBridge(); // Ensure Linea native bridge fee can be covered
        // and there is some ETH actually bridged after deducting the fee

        ILineaMessageService(l2bridge).sendMessage{ value: value }(target, minimumFee, bytes(""));
```

**File:** contracts/pools/RSETHPoolV2.sol (L207-218)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV2.sol (L225-233)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV2.sol (L286-298)
```text
    function bridgeAssets() external nonReentrant onlyRole(BRIDGER_ROLE) {
        UtilLib.checkNonZeroAddress(l2Bridge);
        UtilLib.checkNonZeroAddress(messenger);
        UtilLib.checkNonZeroAddress(l1VaultETHForL2Chain);

        // withdraw ETH - fees
        uint256 ethBalanceMinusFees = getETHBalanceMinusFees();

        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
            l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
        );

        emit BridgedETHToL1ViaNativeBridge(l1VaultETHForL2Chain, ethBalanceMinusFees);
```
