### Title
Zero rsETH Minted to Depositor Due to Inflatable rsETHPrice With No Zero-Shares Guard - (File: `contracts/LRTDepositPool.sol`)

### Summary
`LRTDepositPool._beforeDeposit` computes `rsethAmountToMint` and checks it against `minRSETHAmountExpected`, but never asserts `rsethAmountToMint > 0`. Because `rsETHPrice` is a stored state variable updated by the permissionless `LRTOracle.updateRSETHPrice()`, and because `LRTDepositPool.receive()` accepts arbitrary ETH that is immediately counted in `totalETHInProtocol`, an attacker can inflate the stored price and cause a victim's deposit to mint zero rsETH — permanently losing the victim's ETH to the pool, where the attacker (as the sole rsETH holder) redeems it.

---

### Finding Description

**Root cause 1 — No zero-shares guard in `_beforeDeposit`:** [1](#0-0) 

```solidity
function _beforeDeposit(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected)
    private view returns (uint256 rsethAmountToMint)
{
    if (depositAmount == 0 || depositAmount < minAmountToDeposit) revert InvalidAmountToDeposit();
    ...
    rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
    if (rsethAmountToMint < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    // ← NO: require(rsethAmountToMint > 0)
}
```

When `minRSETHAmountExpected == 0` (the default for many callers) and `rsethAmountToMint` rounds to 0, the check `0 < 0` is `false`, so no revert occurs. `_mintRsETH(0)` is called, minting nothing.

**Root cause 2 — `rsETHPrice` is inflatable via a direct ETH donation:** [2](#0-1) 

`LRTDepositPool` has an open `receive()` payable fallback. Any ETH sent directly to the contract is immediately reflected in `address(this).balance`, which is the value returned as `ethLyingInDepositPool` in `getETHDistributionData()`. [3](#0-2) 

`_getTotalEthInProtocol()` in `LRTOracle` calls `getTotalAssetDeposits(ETH_TOKEN)`, which sums `address(this).balance` of the deposit pool. A direct ETH donation therefore inflates `totalETHInProtocol`.

**Root cause 3 — `updateRSETHPrice()` is permissionless:** [4](#0-3) 

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Anyone can call this to commit the inflated price to storage.

**Root cause 4 — `pricePercentageLimit` defaults to 0, disabling the price-increase guard:** [5](#0-4) 

```solidity
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

Because `pricePercentageLimit` is never set in `initialize()` and defaults to `0`, the guard is permanently disabled until an admin explicitly calls `setPricePercentageLimit`. During this window — which includes the entire initial deployment period — any magnitude of price inflation is accepted.

**Root cause 5 — Mint formula truncates to zero:** [6](#0-5) 

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

Integer division truncates. If `rsETHPrice` is inflated to be larger than `amount * assetPrice`, the result is 0.

---

### Impact Explanation

**Critical — Direct theft of user funds.**

A victim who calls `depositETH(0, "")` (setting `minRSETHAmountExpected = 0`) after the attacker inflates the price will:
- Transfer their ETH to `LRTDepositPool`
- Receive 0 rsETH in return
- Have no recourse — the ETH is now part of the pool's balance

The attacker, holding the only rsETH in existence, redeems it at the inflated price and recovers the victim's ETH plus their own donation (minus the 1-wei seed deposit). The net result is direct theft of the victim's deposited ETH.

---

### Likelihood Explanation

**Medium.** The attack requires:
1. The attacker to be the first (or only) rsETH holder — realistic at protocol launch or after a full withdrawal cycle.
2. `pricePercentageLimit == 0` — this is the default state and persists until admin action.
3. The victim to call `depositETH` with `minRSETHAmountExpected == 0` — common when users rely on frontends that omit slippage protection, or when integrating contracts pass 0.

The ETH cost of the donation equals the victim's deposit size, so the attack is break-even at minimum and profitable when the victim's deposit exceeds the donation. This is the standard inflation-attack economics.

---

### Recommendation

1. **Add a zero-shares guard in `_beforeDeposit`:**
   ```solidity
   if (rsethAmountToMint == 0) revert ZeroSharesMinted();
   ```

2. **Set a non-zero `pricePercentageLimit` during `initialize`** (e.g., 1% = `1e16`) so that large single-block price jumps are rejected for non-manager callers.

3. **Consider rejecting direct ETH donations** by reverting in `receive()` unless the sender is a known NodeDelegator or reward receiver, preventing the donation vector entirely.

---

### Proof of Concept

```
State: rsETHPrice = 1e18, rsethSupply = 0

Step 1 — Attacker seeds the pool:
  attacker.depositETH{value: 1}(0, "")
  → rsethAmountToMint = (1 * 1e18) / 1e18 = 1
  → attacker holds 1 wei rsETH
  → pool ETH balance = 1 wei

Step 2 — Attacker donates ETH directly:
  attacker sends 1e18 wei ETH to LRTDepositPool (via receive())
  → pool ETH balance = 1 + 1e18 wei

Step 3 — Attacker inflates the stored price:
  attacker calls LRTOracle.updateRSETHPrice()
  → totalETHInProtocol = (1 + 1e18) wei
  → rsethSupply = 1 wei
  → newRsETHPrice = (1 + 1e18) * 1e18 / 1 ≈ 1e36
  → pricePercentageLimit == 0 → no revert
  → rsETHPrice = 1e36 stored

Step 4 — Victim deposits:
  victim.depositETH{value: 1e18}(0, "")
  → rsethAmountToMint = (1e18 * 1e18) / 1e36 = 0
  → 0 < 0 is false → no revert
  → _mintRsETH(0) → victim gets 0 rsETH
  → pool ETH balance = 1 + 1e18 + 1e18 = 2e18 + 1 wei

Step 5 — Attacker withdraws:
  attacker redeems 1 wei rsETH at rsETHPrice ≈ 1e36
  → underlyingToReceive = 1 * 1e36 / 1e18 = 1e18 wei = 1 ETH
  → attacker recovers ~1 ETH (victim's deposit), net profit ≈ victim's 1e18 - donation 1e18 = 0
  (break-even; profitable when victim deposit > donation)
```

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-496)
```text
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
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```
