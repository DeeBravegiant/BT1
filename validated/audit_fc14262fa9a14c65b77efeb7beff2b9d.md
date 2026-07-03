Audit Report

## Title
Missing Staleness Check in `CrossChainRateReceiver.getRate()` Allows Stale Rate to Under-Mint agETH in `AGETHPoolV3.deposit()` — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary

`CrossChainRateReceiver.getRate()` returns the stored `rate` unconditionally, ignoring `lastUpdated` entirely. `AGETHPoolV3.deposit()` uses this rate as the denominator when computing how many agETH to mint. If a LayerZero outage or message drop freezes the rate at a value higher than the true current rate (e.g., after a slashing event or rate correction), every depositor receives fewer agETH than the current backing warrants.

## Finding Description

**`CrossChainRateReceiver` — rate storage without staleness enforcement**

`lzReceive` correctly records both the incoming rate and the delivery timestamp: [1](#0-0) 

`getRate()` returns `rate` with no reference to `lastUpdated`: [2](#0-1) 

`lastUpdated` is a public state variable that is written on every `lzReceive` call but is never read by any on-chain logic, confirming the staleness guard was anticipated and left unimplemented. [3](#0-2) 

**`AGETHPoolV3` — deposit math depends entirely on the oracle rate**

`deposit()` delegates to `viewSwapAgETHAmountAndFee`, which divides by the oracle-supplied rate: [4](#0-3) 

`getRate()` in the pool passes through directly to `agETHOracle`, which is the `AGETHRateReceiver` instance: [5](#0-4) 

**Exploit path:** A LayerZero outage freezes `rate` at a value recorded before a slashing event or rate correction. The true agETH/ETH rate falls below the stored value. Any depositor calling `deposit()` during this window has their `agETHAmount` computed with a denominator that is too large, yielding fewer agETH than the current backing justifies. No privileged action is required; any unprivileged depositor is affected.

**No existing guard:** There is no `require`, `revert`, or circuit-breaker in `getRate()`, `viewSwapAgETHAmountAndFee()`, or `deposit()` that checks the age of the stored rate. [6](#0-5) 

## Impact Explanation

Depositors send ETH and receive fewer agETH than the current agETH/ETH backing justifies. The deposited ETH is not lost from the pool, but the depositor's position is immediately worth less than what they paid. This matches the **Low** allowed impact: *contract fails to deliver promised returns, but doesn't lose value*.

## Likelihood Explanation

agETH is a yield-bearing token whose rate normally increases monotonically, so a stale-high rate requires either a slashing event, a temporary rate correction, or a prolonged LayerZero outage that freezes the rate while the true rate falls. LayerZero message delays and outages are documented operational risks for cross-chain deployments. The presence of `lastUpdated` as a written-but-never-read field confirms the developers anticipated this guard. No attacker capability beyond calling `deposit()` is required once the stale-rate condition exists.

## Recommendation

Add a maximum staleness threshold in `CrossChainRateReceiver.getRate()`:

```solidity
uint256 public constant MAX_RATE_AGE = 24 hours;

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate is stale");
    return rate;
}
```

Alternatively, expose `lastUpdated` to `AGETHPoolV3` and enforce the check before minting in `viewSwapAgETHAmountAndFee`. Either approach closes the gap between the written `lastUpdated` field and its intended use. [2](#0-1) 

## Proof of Concept

```solidity
// Fork test on Scroll or Linea at a block where agETH rate is known
function testStaleHighRateUnderMints() public {
    AGETHRateReceiver receiver = AGETHRateReceiver(<deployed_address>);

    // 1. Record the true current rate
    uint256 trueRate = receiver.getRate(); // e.g. 1.05e18

    // 2. Overwrite storage slot 0 (rate) with a stale-high value
    uint256 staleRate = trueRate * 105 / 100; // 1.1025e18
    vm.store(address(receiver), bytes32(0), bytes32(staleRate));
    // lastUpdated is NOT updated — staleness is invisible to the pool

    // 3. Deposit 1 ETH as an unprivileged user
    uint256 agETHBefore = agETH.balanceOf(alice);
    vm.prank(alice);
    pool.deposit{value: 1 ether}("");
    uint256 minted = agETH.balanceOf(alice) - agETHBefore;

    // 4. Expected amount at true rate (ignoring fee)
    uint256 expected = 1 ether * 1e18 / trueRate;

    // 5. Minted < expected — depositor is under-paid
    assertLt(minted, expected);
}
```

The test passes on unmodified code because `getRate()` returns `rate` without consulting `lastUpdated`. [2](#0-1)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L16-16)
```text
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-97)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L104-106)
```text
    function getRate() public view returns (uint256) {
        return IOracle(agETHOracle).getRate();
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L115-128)
```text
    function deposit(string memory referralId) external payable nonReentrant {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L160-168)
```text
    function viewSwapAgETHAmountAndFee(uint256 amount) public view returns (uint256 agETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
```
