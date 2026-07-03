### Title
`setFeeBps` Lacks Timelock Protection Unlike Other Critical Setters - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
`RSETHPoolV3.setFeeBps` is guarded only by `DEFAULT_ADMIN_ROLE`, while every other critical setter in the same contract is guarded by the purpose-built `TIMELOCK_ROLE`. An admin can instantly raise the deposit fee to 10% (1000 bps) with no delay, causing depositors to receive materially less rsETH than expected with no opportunity to react.

### Finding Description
`RSETHPoolV3` defines a dedicated `TIMELOCK_ROLE` and consistently applies it to all sensitive configuration setters:

| Function | Guard |
|---|---|
| `setIsEthDepositEnabled` | `TIMELOCK_ROLE` |
| `setRSETHOracle` | `TIMELOCK_ROLE` |
| `addSupportedToken` | `TIMELOCK_ROLE` |
| `removeSupportedToken` | `TIMELOCK_ROLE` |
| `setSupportedTokenOracle` | `TIMELOCK_ROLE` |
| **`setFeeBps`** | **`DEFAULT_ADMIN_ROLE` — no timelock** |

`setFeeBps` is the single exception. It allows the `DEFAULT_ADMIN_ROLE` holder to change `feeBps` from 0 to 1000 (10%) atomically in one transaction, with no queuing delay.

The fee is applied at deposit time inside `viewSwapRsETHAmountAndFee`:

```solidity
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

A user who previews their deposit at 0 bps and submits the transaction in the same block as a `setFeeBps(1000)` call will receive 10% fewer rsETH tokens than the preview showed, with no recourse.

The same inconsistency is present in `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3WithNativeChainBridge.sol`, and `AGETHPoolV3.sol`, all of which share the same `setFeeBps` / `TIMELOCK_ROLE` pattern.

### Impact Explanation
A depositor who calls `viewSwapRsETHAmountAndFee` to preview their rsETH output and then submits a deposit transaction will receive up to 10% fewer rsETH tokens than previewed if `setFeeBps` is called in the interim. The deposited ETH/LST is not returned; the shortfall is silently captured as protocol fee. This constitutes a contract failing to deliver the promised return to the user.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

### Likelihood Explanation
The `DEFAULT_ADMIN_ROLE` holder can call `setFeeBps` at any time. No external trigger or exploit is required. The protocol's own design intent — evidenced by the consistent use of `TIMELOCK_ROLE` on every other setter — confirms that fee changes were intended to be time-locked. The omission is an implementation gap, not a deliberate design choice.

### Recommendation
Change the access modifier on `setFeeBps` (and `setDailyMintLimit`, which has the same gap) from `DEFAULT_ADMIN_ROLE` to `TIMELOCK_ROLE`, consistent with every other critical setter in the contract:

```solidity
function setFeeBps(uint256 _feeBps) external onlyRole(TIMELOCK_ROLE) {
    if (_feeBps > 1000) revert InvalidFeeAmount();
    feeBps = _feeBps;
    emit FeeBpsSet(_feeBps);
}
```

Apply the same fix to `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, and `AGETHPoolV3`.

### Proof of Concept

1. Alice calls `viewSwapRsETHAmountAndFee(1 ether)` at `feeBps = 0`. She sees she will receive `X` rsETH.
2. Admin calls `setFeeBps(1000)` — instant, no timelock, no delay.
3. Alice's deposit transaction executes in the same or next block.
4. Alice receives `0.9 * X` rsETH — 10% less than previewed — with no warning and no ability to cancel.

The root cause is the inconsistent access control on `setFeeBps`: [1](#0-0) 

Compared to the timelock-protected setters in the same contract: [2](#0-1) [3](#0-2) 

The fee calculation that uses `feeBps` directly at deposit time: [4](#0-3)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L516-522)
```text
    /// @dev Sets the fee basis points
    /// @param _feeBps The fee basis points
    function setFeeBps(uint256 _feeBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_feeBps > 1000) revert InvalidFeeAmount();
        feeBps = _feeBps;
        emit FeeBpsSet(_feeBps);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L526-529)
```text
    function setIsEthDepositEnabled(bool _isEthDepositEnabled) external onlyRole(TIMELOCK_ROLE) {
        isEthDepositEnabled = _isEthDepositEnabled;
        emit IsEthDepositEnabled(_isEthDepositEnabled);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L533-537)
```text
    function setRSETHOracle(address _rsETHOracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(_rsETHOracle);
        rsETHOracle = _rsETHOracle;
        emit OracleSet(_rsETHOracle);
    }
```
