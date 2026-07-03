### Title
Zero agETH Minted on Dust Deposit with No Refund — (`contracts/agETH/AGETHPoolV3.sol`)

### Summary

`deposit(address,uint256,string)` transfers the depositor's tokens into the contract **before** computing `agETHAmount`. When the deposit is small enough that `amountAfterFee * tokenToETHRate / agETHToETHrate` truncates to zero, `agETH.mint(msg.sender, 0)` is called — which succeeds silently under OpenZeppelin's ERC20 — and the depositor's tokens are permanently retained by the contract with no refund. The same truncation applies to the ETH path via `viewSwapAgETHAmountAndFee(uint256)`.

---

### Finding Description

In `deposit(address token, uint256 amount, string referralId)`:

1. **Tokens are pulled first** (line 145), before any output amount is verified.
2. `agETHAmount` is computed via `viewSwapAgETHAmountAndFee(amount, token)` (line 147), which performs integer division:
   ```
   agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;   // line 194
   ```
   For any `amount` where `amountAfterFee * tokenToETHRate < agETHToETHrate`, this rounds down to **0**.
3. There is **no guard** checking `agETHAmount > 0` before minting.
4. `agETH.mint(msg.sender, 0)` is called (line 151). OpenZeppelin's `_mint` does **not** revert on a zero amount — it simply adds 0 to the balance and emits a `Transfer(address(0), account, 0)` event.
5. There is **no refund path**. The depositor's tokens remain in the contract, claimable only by `BRIDGER_ROLE` via `moveAssetsForBridging`. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

The depositor loses their tokens permanently. The funds are not returned and no agETH is issued in exchange. The tokens accumulate in the contract and are swept by the bridger. This is a direct loss of depositor funds in-motion, matching the **Critical** scope: *Direct theft of any user funds, whether at-rest or in-motion*.

The practical loss threshold per transaction is:

| Token | Condition for zero agETH | Max loss per tx |
|---|---|---|
| 18-decimal token (e.g. wstETH, `tokenToETHRate ≈ 1e18`) | `amount < agETHToETHrate / 1e18 ≈ 1–2 wei` | ~2 wei |
| 6-decimal token (e.g. USDC, `tokenToETHRate ≈ 3.33e14`) | `amount < agETHToETHrate / 3.33e14 ≈ 3154 units` | ~0.003 USDC |

While individual losses are small, the invariant is unconditionally broken for any deposit below the threshold, and the loss is permanent with no recovery path for the user.

---

### Likelihood Explanation

Any unprivileged user can trigger this by calling `deposit(token, smallAmount, "")` with a dust amount. No special role, no front-running, no oracle manipulation required. The condition is deterministic and reproducible on any fork or local test.

---

### Recommendation

Add a zero-output guard immediately after computing `agETHAmount`, before the token transfer (or revert after transfer):

```solidity
(uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);
if (agETHAmount == 0) revert InvalidAmount();
```

Alternatively, enforce a minimum deposit amount that guarantees `agETHAmount >= 1` given the current oracle rates. [1](#0-0) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Assumptions (local fork / unit test):
//   agETHToETHrate = 1.05e18  (agETH has appreciated)
//   tokenToETHRate = 1e18     (token is 1:1 with ETH, 18 decimals)
//   feeBps = 0

// viewSwapAgETHAmountAndFee(1, token):
//   fee           = 1 * 0 / 10_000 = 0
//   amountAfterFee = 1
//   agETHAmount   = 1 * 1e18 / 1.05e18 = 0   ← truncated

// deposit(token, 1, ""):
//   safeTransferFrom(msg.sender, contract, 1)  ← 1 wei token pulled
//   agETHAmount = 0
//   feeEarnedInToken[token] += 0
//   agETH.mint(msg.sender, 0)                  ← succeeds, emits Transfer(0, user, 0)
//   user balance of agETH: unchanged (0 minted)
//   contract balance of token: +1 wei (never returned)

// Assert: agETH.balanceOf(depositor) == 0
// Assert: token.balanceOf(contract)  == 1  (depositor's token is gone)
```

The same truncation applies to the ETH path at line 168 (`agETHAmount = amountAfterFee * 1e18 / agETHToETHrate`), where 1 wei of ETH sent to `deposit(string)` also yields 0 agETH with no ETH refund. [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/agETH/AGETHPoolV3.sol (L119-128)
```text
        if (amount == 0) revert InvalidAmount();

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L143-154)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L160-169)
```text
    function viewSwapAgETHAmountAndFee(uint256 amount) public view returns (uint256 agETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L183-195)
```text
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
    }
```

**File:** lib/openzeppelin-contracts-upgradeable/contracts/token/ERC20/ERC20Upgradeable.sol (L256-268)
```text
    function _mint(address account, uint256 amount) internal virtual {
        require(account != address(0), "ERC20: mint to the zero address");

        _beforeTokenTransfer(address(0), account, amount);

        _totalSupply += amount;
        unchecked {
            // Overflow not possible: balance + amount is at most totalSupply + amount, which is checked above.
            _balances[account] += amount;
        }
        emit Transfer(address(0), account, amount);

        _afterTokenTransfer(address(0), account, amount);
```
