### Title
Inconsistent `token` and `currentMerkleRoot` allow users to receive wrong-denomination token amounts when `setToken` is called independently of `setMerkleRoot` - (File: contracts/utils/MerkleDistributor/MerkleDistributor.sol)

### Summary
`MerkleDistributor` exposes `setToken()` and `setMerkleRoot()` as independent admin operations. The merkle tree encodes `cumulativeAmount` values denominated in the currently distributed token. If the admin changes `token` without atomically updating `currentMerkleRoot` (or vice versa), any user who calls `claim()` between the two transactions receives the new token in amounts that were denominated for the old token, producing a decimal-scale mismatch identical in structure to the reference report.

### Finding Description
`MerkleDistributor` stores two independently mutable state variables that must be consistent with each other:

- `token` — the ERC20 address from which claims are paid out, settable via `setToken()`.
- `currentMerkleRoot` — the root whose leaves encode `(index, account, cumulativeAmount)` tuples, where `cumulativeAmount` is implicitly denominated in the token that was active when the root was published, settable via `setMerkleRoot()`. [1](#0-0) [2](#0-1) 

The `initialize` function explicitly permits `token_` to be `address(0)` at deployment time (the comment reads *"token can be set later but not the protocol treasury"*), confirming that `setToken()` is an expected operational path, not an emergency escape hatch. [3](#0-2) 

When `claim()` executes, it reads `token` at call time and transfers `claimableAmount` (derived from the merkle-encoded `cumulativeAmount`) of that token to the user: [4](#0-3) 

There is no check that the token active at claim time matches the token that was active when the merkle root was published.

### Impact Explanation
If the admin calls `setToken(newToken)` while the existing `currentMerkleRoot` still encodes amounts denominated in `oldToken`:

- **Old token 18-decimal → new token 6-decimal**: a user whose leaf says `cumulativeAmount = 1e18` (= 1 whole old token) now receives `1e18` units of the 6-decimal new token, i.e. **1 000 000 000 000 whole new tokens** — draining the contract and stealing yield from the protocol.
- **Old token 6-decimal → new token 18-decimal**: a user whose leaf says `cumulativeAmount = 1e6` (= 1 whole old token) now receives `1e6` units of the 18-decimal new token, i.e. **0.000000000001 whole new tokens** — stealing unclaimed yield from the user.

Both directions constitute theft of unclaimed yield (HIGH impact).

### Likelihood Explanation
Medium. The `initialize` function is explicitly designed to allow `token` to be set post-deployment, making `setToken()` a routine operational call. An admin migrating the distributed token (e.g., upgrading from a v1 to a v2 token) would naturally call `setToken()` first and `setMerkleRoot()` in a subsequent transaction. Any user claim landing in that window suffers the mismatch. No attacker action is required beyond calling the public `claim()` function.

### Recommendation
Require that `setToken` and `setMerkleRoot` are always called atomically (e.g., via a combined setter that updates both in one transaction), or store the token address inside each merkle leaf and verify it on-chain during `claim()`. At minimum, add a `require(token != address(0))` guard before any claim is processed and document that `setToken` must always be paired with a `setMerkleRoot` call in the same transaction.

### Proof of Concept
1. Owner deploys `MerkleDistributor` with `token = address(0)`.
2. Owner publishes a merkle root whose leaves encode amounts in USDC (6 decimals). Owner calls `setToken(USDC)`.
3. Owner decides to migrate to DAI (18 decimals). Owner calls `setToken(DAI)` in tx A.
4. Before tx B (`setMerkleRoot` with DAI-denominated amounts) is mined, Alice calls `claim(index, alice, 1_000_000 /*= 1 USDC*/, proof)`.
5. `claim()` reads `token = DAI` and executes `IERC20(DAI).safeTransfer(alice, 1_000_000)` — Alice receives `0.000000000001 DAI` instead of `1 DAI` worth of value. Her unclaimed yield is stolen.
6. Conversely, if the decimal direction is reversed (18→6), Alice drains the contract. [5](#0-4)

### Citations

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L71-87)
```text
    function initialize(address token_, address _protocolTreasury, uint256 _feeInBPS) external initializer {
        // token can be set later but not the protocol treasury
        if (_protocolTreasury == address(0)) {
            revert ZeroValueProvided();
        }

        if (_feeInBPS > MAX_FEE_IN_BPS) {
            revert InvalidFeeInBPS();
        }

        __Ownable_init();
        __Pausable_init();

        token = token_;
        protocolTreasury = _protocolTreasury;
        feeInBPS = _feeInBPS;
    }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L137-144)
```text
        // Send the claimable amount to the user - deducting the fee
        uint256 fee = (claimableAmount * feeInBPS) / 10_000;
        uint256 amountToSend = claimableAmount - fee;

        IERC20(token).safeTransfer(account, amountToSend);

        // Send the fee to the protocol treasury
        IERC20(token).safeTransfer(protocolTreasury, fee);
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L156-167)
```text
    function setMerkleRoot(bytes32 _merkleRootToSet) external onlyOwner {
        if (_merkleRootToSet == bytes32(0)) {
            revert ZeroValueProvided();
        }

        currentMerkleRoot = _merkleRootToSet;

        currentMerkleRootIndex++;
        currentIndex++;

        emit MerkleRootSet(currentMerkleRootIndex, currentMerkleRoot);
    }
```

**File:** contracts/utils/MerkleDistributor/MerkleDistributor.sol (L185-193)
```text
    function setToken(address _token) external onlyOwner {
        if (_token == address(0)) {
            revert ZeroValueProvided();
        }

        token = _token;

        emit TokenUpdated(_token);
    }
```
