### Title
`claimStEth`/`claimSwEth` Sweep Entire ETH Balance Instead of Specific Claim Amount, Corrupting `ethValueInWithdrawal` Accounting - (File: contracts/LRTConverter.sol)

### Summary
`LRTConverter.claimStEth` and `claimSwEth` pass `address(this).balance` to `_sendEthToDepositPool` instead of the amount received from the specific Lido/Swell claim. Any ETH sitting in the contract from external sources (e.g., direct transfers via the public `receive()` function) is swept along with the claim proceeds, causing `ethValueInWithdrawal` to be decremented by more than the actual claim amount. Because `ethValueInWithdrawal` feeds directly into the rsETH price calculation, the price becomes understated, allowing new depositors to mint rsETH at a discount at the expense of existing holders.

### Finding Description

`LRTConverter` tracks the ETH value of assets currently in the unstaking pipeline via `ethValueInWithdrawal`. This variable is incremented in `transferAssetFromDepositPool` and decremented inside `_sendEthToDepositPool`. [1](#0-0) 

Both claim functions call `_sendEthToDepositPool(address(this).balance)`: [2](#0-1) 

Inside `_sendEthToDepositPool`, the passed amount is used to decrement `ethValueInWithdrawal`: [3](#0-2) 

If the contract holds any ETH beyond the specific claim proceeds (e.g., from a direct transfer via the public `receive()` fallback), `address(this).balance` exceeds the actual claim amount. The surplus is swept to the deposit pool, but `ethValueInWithdrawal` is decremented by the full balance rather than just the claim amount, leaving it understated — or zeroed out entirely while pending withdrawals remain. [4](#0-3) 

`ethValueInWithdrawal` is read by `getETHDistributionData` as `ethLyingInConverter`: [5](#0-4) 

This feeds into `getTotalAssetDeposits(ETH_TOKEN)`: [6](#0-5) 

Which is consumed by `_getTotalEthInProtocol()` in `LRTOracle`: [7](#0-6) 

Which determines the rsETH price: [8](#0-7) 

### Impact Explanation

When `ethValueInWithdrawal` is prematurely decremented below its correct value, `_getTotalEthInProtocol()` returns a lower figure than the true protocol TVL. The rsETH price computed from this understated TVL is lower than it should be. New depositors who mint rsETH during this window receive more rsETH per ETH than they are entitled to, diluting the share of existing rsETH holders. This constitutes theft of unclaimed yield from existing holders.

**Impact: High** — theft of unclaimed yield.

### Likelihood Explanation

The precondition is that LRTConverter holds ETH from a source other than the specific claim being processed. The `receive()` function is public and payable, so any address can send ETH to the contract at any time. Once ETH is present, the next routine operator call to `claimStEth` or `claimSwEth` (normal protocol operation, not a compromise) triggers the accounting error. The operator is not acting maliciously; the bug is in the code. The attack is not self-profitable (the sender loses the donated ETH), so deliberate exploitation is unlikely, but the condition can arise accidentally (e.g., ETH sent by mistake, `selfdestruct` proceeds, or MEV bots).

**Likelihood: Low.**

### Recommendation

Track the ETH balance before and after the claim, and pass only the received amount to `_sendEthToDepositPool`:

```diff
  function claimStEth(uint256 _requestId, uint256 _hint) external nonReentrant onlyLRTOperator {
+     uint256 balanceBefore = address(this).balance;
      _claimStEth(_requestId, _hint);
-     _sendEthToDepositPool(address(this).balance);
+     _sendEthToDepositPool(address(this).balance - balanceBefore);
  }

  function claimSwEth(uint256 _tokenId) external nonReentrant onlyLRTOperator {
+     uint256 balanceBefore = address(this).balance;
      _claimSwEth(_tokenId);
-     _sendEthToDepositPool(address(this).balance);
+     _sendEthToDepositPool(address(this).balance - balanceBefore);
  }
```

### Proof of Concept

1. Protocol state: 100 ETH of stETH is in Lido's withdrawal queue; `ethValueInWithdrawal = 100 ETH`. Deposit pool holds 0 ETH.
2. An external address sends 10 ETH directly to `LRTConverter` via `receive()`. `address(this).balance = 10 ETH`. This ETH is **not** counted in `ethValueInWithdrawal` or anywhere else in the TVL.
3. Operator calls `claimStEth(requestId, hint)` for a 50 ETH Lido request. Lido sends 50 ETH to `LRTConverter`. `address(this).balance = 60 ETH`.
4. `_sendEthToDepositPool(60)` is called. `ethValueInWithdrawal = 100 - 60 = 40 ETH` (correct value would be `100 - 50 = 50 ETH`). 60 ETH is forwarded to the deposit pool.
5. Protocol TVL is now computed as: `ethLyingInConverter = 40 ETH` + `ethLyingInDepositPool = 60 ETH` = 100 ETH. The correct TVL is `50 + 60 = 110 ETH` (the 10 ETH donation is now in the pool and should be counted, but `ethValueInWithdrawal` was over-decremented by 10 ETH, netting a 10 ETH undercount).
6. rsETH price is understated. Any depositor who mints rsETH before the remaining 50 ETH Lido claim is processed receives more rsETH than entitled, diluting existing holders. [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** contracts/LRTConverter.sol (L117-118)
```text
    /// @dev fallback to receive funds
    receive() external payable { }
```

**File:** contracts/LRTConverter.sol (L179-194)
```text
    /// @notice claim eth from lido for steth and sends to deposit pool
    function claimStEth(uint256 _requestId, uint256 _hint) external nonReentrant onlyLRTOperator {
        _claimStEth(_requestId, _hint);
        _sendEthToDepositPool(address(this).balance);
    }

    /// @notice raises a unstake request for sweth on swell
    function unstakeSwEth(uint256 amountToUnstake) external nonReentrant onlyLRTOperator {
        _unstakeSwEth(amountToUnstake);
    }

    /// @notice claim eth from sweth from swell for sweth and sends to deposit pool
    function claimSwEth(uint256 _tokenId) external nonReentrant onlyLRTOperator {
        _claimSwEth(_tokenId);
        _sendEthToDepositPool(address(this).balance);
    }
```

**File:** contracts/LRTConverter.sol (L252-263)
```text
    function _sendEthToDepositPool(uint256 _amount) internal {
        address lrtDepositPoolAddress = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ethValueInWithdrawal > _amount) {
            ethValueInWithdrawal -= _amount;
        } else {
            ethValueInWithdrawal = 0;
        }
        // Send eth to deposit pool
        ILRTDepositPool(lrtDepositPoolAddress).receiveFromLRTConverter{ value: _amount }();
        emit EthTransferred(lrtDepositPoolAddress, _amount);
    }
```

**File:** contracts/LRTDepositPool.sol (L385-397)
```text
    function getTotalAssetDeposits(address asset) public view override returns (uint256 totalAssetDeposit) {
        (
            uint256 assetLyingInDepositPool,
            uint256 assetLyingInNDCs,
            uint256 assetStakedInEigenLayer,
            uint256 assetUnstakingFromEigenLayer,
            uint256 assetLyingInConverter,
            uint256 assetLyingUnstakingVault
        ) = getAssetDistributionData(asset);
        uint256 effectiveAssetWithEigenLayer = assetStakedInEigenLayer + assetUnstakingFromEigenLayer;
        return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
                + assetLyingUnstakingVault);
    }
```

**File:** contracts/LRTDepositPool.sol (L498-500)
```text
        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
    }
```

**File:** contracts/LRTOracle.sol (L249-251)
```text
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
