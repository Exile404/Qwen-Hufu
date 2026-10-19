// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {P256} from "@openzeppelin/contracts/utils/cryptography/P256.sol";
import {WebAuthn} from "@openzeppelin/contracts/utils/cryptography/WebAuthn.sol";

interface IFXPool {
    function tokenOutFor(address tokenIn) external view returns (address);
    function swap(address tokenIn, uint256 amountIn, uint256 minOut, address to) external returns (uint256);
}

/// @title HufuVault
/// @notice A tiger tally (虎符) for an AI agent that moves money.
///         The agent holds the commander's half: it can pay saved payees within caps, run due
///         mandates and convert currency, all on its own (T1). Anything bigger goes through
///         `requestPayment` and needs the owner's half (`approveRequest`, T2). Policy changes are
///         owner-only, so the agent has no path to them (T3). As on the Du Tiger Tally, an
///         emergency needs no matching: the agent, guardian or owner can pause, only the owner unpauses.
///         The owner's half can live on the student's device as a passkey: every owner action can be
///         signed with WebAuthn and verified here (`executeWithPasskey`), and once the passkey has
///         signed, the owner key can be locked so that only the passkey can act as owner.
contract HufuVault is ReentrancyGuard {
    using SafeERC20 for IERC20;

    struct TokenPolicy {
        bool enabled;
        uint256 perTxCap;
        uint256 dailyCap;
    }

    struct Payee {
        bool allowed;
        string label;
    }

    struct Mandate {
        address payee;
        address token;
        uint256 amount;
        uint64 interval; // seconds between payments; 0 = one-off
        uint64 nextDue;
        uint64 end; // last allowed due time; 0 = open-ended
        bool active;
        string label;
    }

    enum RequestStatus {
        None,
        Pending,
        Approved,
        Rejected
    }

    struct PaymentRequest {
        address token;
        address to;
        uint256 amount;
        bytes32 memoHash;
        uint64 createdAt;
        RequestStatus status;
    }

    address public owner;
    address public agent;
    uint64 public agentExpiry;
    address public guardian;
    address public fxPool;
    bool public paused;

    mapping(address token => TokenPolicy) public tokenPolicy;
    mapping(address payee => Payee) public payees;
    /// @dev Optional per-payee, per-token cap on what the agent pays that payee per UTC day.
    ///      0 = only the token caps apply.
    mapping(address payee => mapping(address token => uint256)) public payeeCap;
    /// @dev Agent spend per token per UTC day (block.timestamp / 1 days).
    mapping(address token => mapping(uint256 day => uint256)) public spentOnDay;
    /// @dev Agent payments per payee per token per UTC day, checked against payeeCap.
    mapping(address payee => mapping(address token => mapping(uint256 day => uint256))) public
        paidToPayeeOnDay;

    Mandate[] internal _mandates;
    PaymentRequest[] internal _requests;

    /// @notice Domain tag of what the passkey signs (see `passkeyDigest`).
    bytes32 public constant PASSKEY_DOMAIN = keccak256("HufuVault.executeWithPasskey.v1");
    /// @notice The student's passkey: a P-256 public key from WebAuthn. (0, 0) = none registered.
    bytes32 public passkeyX;
    bytes32 public passkeyY;
    /// @notice Passkey-signed actions so far. Each signature is bound to one nonce, so it works once.
    uint256 public passkeyNonce;
    /// @notice Once true, the owner key has no power left: only the passkey can act as owner.
    bool public ownerKeyRetired;

    // --- agent actions
    event Paid(address indexed token, address indexed to, uint256 amount, bytes32 memoHash);
    event PaymentRequested(
        uint256 indexed id, address indexed token, address indexed to, uint256 amount, bytes32 memoHash
    );
    event MandateExecuted(
        uint256 indexed id,
        address indexed token,
        address indexed payee,
        uint256 amount,
        uint64 paidFor,
        uint64 nextDue
    );
    event Swapped(address indexed tokenIn, address indexed tokenOut, uint256 amountIn, uint256 amountOut);
    event AuditAnchored(bytes32 indexed head, uint256 timestamp);
    // --- owner actions
    event RequestApproved(uint256 indexed id);
    event RequestRejected(uint256 indexed id);
    event MandateCreated(
        uint256 indexed id,
        address indexed payee,
        address indexed token,
        uint256 amount,
        uint64 interval,
        uint64 firstDue,
        uint64 end,
        string label
    );
    event MandateCancelled(uint256 indexed id);
    event TokenPolicySet(address indexed token, bool enabled, uint256 perTxCap, uint256 dailyCap);
    event PayeeSet(address indexed payee, bool allowed, string label);
    event PayeeCapSet(address indexed payee, address indexed token, uint256 cap);
    event AgentSet(address indexed agent, uint64 expiry);
    event GuardianSet(address indexed guardian);
    event FxPoolSet(address indexed fxPool);
    event Withdrawn(address indexed token, address indexed to, uint256 amount);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event PasskeySet(bytes32 x, bytes32 y, bytes credentialId);
    event PasskeyUsed(uint256 indexed nonce, bytes4 indexed action);
    event OwnerKeyRetired(bool retired);
    // --- emergency
    event Paused(address indexed by);
    event Unpaused(address indexed by);

    error NotOwner();
    error NotAgent();
    error NotAllowedToPause();
    error IsPaused();
    error AgentExpired(uint64 expiry);
    error ZeroAddress();
    error ZeroAmount();
    error TokenNotEnabled(address token);
    error PayeeNotAllowed(address payee);
    error OverPerTxCap(uint256 amount, uint256 cap);
    error OverPayeeCap(uint256 amount, uint256 leftToday);
    error OverDailyCap(uint256 spentAfter, uint256 cap);
    error UnknownMandate(uint256 id);
    error MandateNotActive(uint256 id);
    error MandateNotDue(uint256 id, uint64 nextDue);
    error MandateEnded(uint256 id);
    error UnknownRequest(uint256 id);
    error RequestNotPending(uint256 id);
    error NoFxPool();
    error NoPasskey();
    error InvalidPasskey();
    error PasskeyExpired(uint64 validUntil);
    error BadPasskeySignature();
    error OnlyViaPasskey();
    error OwnerKeyLocked();
    error NoCall();

    /// @dev The student's half: the owner key (until it is retired), or the student's passkey. A passkey
    ///      action reaches the vault as a call from the vault itself, made only by `executeWithPasskey`
    ///      after the signature checks out; nothing else in the vault calls itself.
    modifier onlyOwner() {
        if (msg.sender != address(this) && (msg.sender != owner || ownerKeyRetired)) revert NotOwner();
        _;
    }

    modifier onlyAgent() {
        if (msg.sender != agent) revert NotAgent();
        _;
    }

    /// @dev The commander's half only works while the vault is live and the session key is valid.
    modifier agentCanAct() {
        if (msg.sender != agent) revert NotAgent();
        if (paused) revert IsPaused();
        // forge-lint: disable-next-line(block-timestamp) -- session expiry is days long
        if (block.timestamp > agentExpiry) revert AgentExpired(agentExpiry);
        _;
    }

    modifier whenNotPaused() {
        if (paused) revert IsPaused();
        _;
    }

    /// @dev agent_ and guardian_ may be zero (no agent yet / no guardian).
    // forge-lint: disable-next-item(missing-zero-check)
    constructor(address owner_, address agent_, uint64 agentExpiry_, address guardian_) {
        if (owner_ == address(0)) revert ZeroAddress();
        owner = owner_;
        agent = agent_;
        agentExpiry = agentExpiry_;
        guardian = guardian_;
        emit OwnershipTransferred(address(0), owner_);
        emit AgentSet(agent_, agentExpiry_);
        emit GuardianSet(guardian_);
    }

    // ------------------------------------------------------------------
    // Agent: the commander's half (T1, plus filing T2 requests)
    // ------------------------------------------------------------------

    /// @notice Pay a saved payee within the per-tx, per-payee and daily caps.
    function pay(address token, address to, uint256 amount, bytes32 memoHash)
        external
        nonReentrant
        agentCanAct
    {
        if (!payees[to].allowed) revert PayeeNotAllowed(to);
        _spendToPayee(token, to, amount);
        _spend(token, amount);
        IERC20(token).safeTransfer(to, amount);
        emit Paid(token, to, amount, memoHash);
    }

    /// @notice File a payment that needs the owner's approval (new payee, over cap, flagged by the guard).
    function requestPayment(address token, address to, uint256 amount, bytes32 memoHash)
        external
        agentCanAct
        returns (uint256 id)
    {
        if (to == address(0)) revert ZeroAddress();
        if (amount == 0) revert ZeroAmount();
        if (!tokenPolicy[token].enabled) revert TokenNotEnabled(token);
        id = _requests.length;
        // forge-lint: disable-next-line(unsafe-typecast) -- uint64 seconds overflow in ~584 billion years
        uint64 createdAt = uint64(block.timestamp);
        _requests.push(PaymentRequest(token, to, amount, memoHash, createdAt, RequestStatus.Pending));
        emit PaymentRequested(id, token, to, amount, memoHash);
    }

    /// @notice Run a mandate the owner signed earlier: exactly its amount, only when due,
    ///         and only while the payee is still allowed.
    function executeMandate(uint256 id) external nonReentrant agentCanAct {
        if (id >= _mandates.length) revert UnknownMandate(id);
        Mandate storage m = _mandates[id];
        if (!m.active) revert MandateNotActive(id);
        // forge-lint: disable-next-line(block-timestamp) -- schedules are days apart; validator drift is seconds
        if (block.timestamp < m.nextDue) revert MandateNotDue(id, m.nextDue);
        if (m.end != 0 && m.nextDue > m.end) revert MandateEnded(id);
        if (!payees[m.payee].allowed) revert PayeeNotAllowed(m.payee);
        if (!tokenPolicy[m.token].enabled) revert TokenNotEnabled(m.token);

        uint64 paidFor = m.nextDue;
        if (m.interval == 0) m.active = false;
        else m.nextDue += m.interval;

        IERC20(m.token).safeTransfer(m.payee, m.amount);
        emit MandateExecuted(id, m.token, m.payee, m.amount, paidFor, m.nextDue);
    }

    /// @notice Convert through the registered FX pool. Counts toward the daily cap of `tokenIn`.
    /// @dev nonReentrant; spend is recorded before the call; the pool is owner-set (trusted).
    // forge-lint: disable-next-item(reentrancy-no-eth, reentrancy-events)
    function swap(address tokenIn, uint256 amountIn, uint256 minOut)
        external
        nonReentrant
        agentCanAct
        returns (uint256 amountOut)
    {
        address pool = fxPool;
        if (pool == address(0)) revert NoFxPool();
        address tokenOut = IFXPool(pool).tokenOutFor(tokenIn);
        if (!tokenPolicy[tokenOut].enabled) revert TokenNotEnabled(tokenOut);
        _spend(tokenIn, amountIn);
        IERC20(tokenIn).forceApprove(pool, amountIn);
        amountOut = IFXPool(pool).swap(tokenIn, amountIn, minOut, address(this));
        IERC20(tokenIn).forceApprove(pool, 0);
        emit Swapped(tokenIn, tokenOut, amountIn, amountOut);
    }

    /// @notice Commit the head of the off-chain hash-chained audit log.
    function anchorAudit(bytes32 head) external onlyAgent {
        emit AuditAnchored(head, block.timestamp);
    }

    // ------------------------------------------------------------------
    // Emergency: the beacon-fire rule (no matching needed to stop)
    // ------------------------------------------------------------------

    function pause() external {
        if (msg.sender != owner && msg.sender != agent && msg.sender != guardian) revert NotAllowedToPause();
        paused = true;
        emit Paused(msg.sender);
    }

    function unpause() external onlyOwner {
        paused = false;
        emit Unpaused(msg.sender);
    }

    // ------------------------------------------------------------------
    // Owner: the ruler's half (T2 approvals and all policy, which is T3 for the agent)
    // ------------------------------------------------------------------

    function approveRequest(uint256 id) external nonReentrant onlyOwner whenNotPaused {
        PaymentRequest storage r = _pendingRequest(id);
        r.status = RequestStatus.Approved;
        IERC20(r.token).safeTransfer(r.to, r.amount);
        emit RequestApproved(id);
    }

    function rejectRequest(uint256 id) external onlyOwner {
        _pendingRequest(id).status = RequestStatus.Rejected;
        emit RequestRejected(id);
    }

    function createMandate(
        address payee,
        address token,
        uint256 amount,
        uint64 interval,
        uint64 firstDue,
        uint64 end,
        string calldata label
    ) external onlyOwner returns (uint256 id) {
        if (!payees[payee].allowed) revert PayeeNotAllowed(payee);
        if (!tokenPolicy[token].enabled) revert TokenNotEnabled(token);
        if (amount == 0) revert ZeroAmount();
        id = _mandates.length;
        _mandates.push(Mandate(payee, token, amount, interval, firstDue, end, true, label));
        emit MandateCreated(id, payee, token, amount, interval, firstDue, end, label);
    }

    function cancelMandate(uint256 id) external onlyOwner {
        if (id >= _mandates.length) revert UnknownMandate(id);
        _mandates[id].active = false;
        emit MandateCancelled(id);
    }

    function setTokenPolicy(address token, bool enabled, uint256 perTxCap, uint256 dailyCap)
        external
        onlyOwner
    {
        if (token == address(0)) revert ZeroAddress();
        tokenPolicy[token] = TokenPolicy(enabled, perTxCap, dailyCap);
        emit TokenPolicySet(token, enabled, perTxCap, dailyCap);
    }

    function setPayee(address payee, bool allowed, string calldata label) external onlyOwner {
        if (payee == address(0)) revert ZeroAddress();
        payees[payee] = Payee(allowed, label);
        emit PayeeSet(payee, allowed, label);
    }

    function setPayeeCap(address payee, address token, uint256 cap) external onlyOwner {
        payeeCap[payee][token] = cap;
        emit PayeeCapSet(payee, token, cap);
    }

    /// @dev agent_ = address(0) revokes the agent.
    // forge-lint: disable-next-item(missing-zero-check)
    function setAgent(address agent_, uint64 expiry) external onlyOwner {
        agent = agent_;
        agentExpiry = expiry;
        emit AgentSet(agent_, expiry);
    }

    /// @dev guardian_ = address(0) removes the guardian.
    // forge-lint: disable-next-item(missing-zero-check)
    function setGuardian(address guardian_) external onlyOwner {
        guardian = guardian_;
        emit GuardianSet(guardian_);
    }

    /// @dev fxPool_ = address(0) disables swaps.
    // forge-lint: disable-next-item(missing-zero-check)
    function setFxPool(address fxPool_) external onlyOwner {
        fxPool = fxPool_;
        emit FxPoolSet(fxPool_);
    }

    /// @notice Owner can always move funds out, even while paused (rescue path).
    function withdraw(address token, address to, uint256 amount) external nonReentrant onlyOwner {
        if (to == address(0)) revert ZeroAddress();
        IERC20(token).safeTransfer(to, amount);
        emit Withdrawn(token, to, amount);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    // ------------------------------------------------------------------
    // The student's passkey: the owner's half on the student's own device
    // ------------------------------------------------------------------

    /// @notice Register or replace the student's passkey. `credentialId` is only logged, so the
    ///         dashboard knows which passkey to ask for.
    // forge-lint: disable-next-item(reentrancy-events) -- P256 is an internal pure library, not an external call
    function setPasskey(bytes32 x, bytes32 y, bytes calldata credentialId) external onlyOwner {
        if (!P256.isValidPublicKey(x, y)) revert InvalidPasskey();
        passkeyX = x;
        passkeyY = y;
        emit PasskeySet(x, y, credentialId);
    }

    /// @notice Remove the passkey, back to the owner key alone. Refused while the owner key is locked, so
    ///         the vault always keeps a working owner.
    function removePasskey() external onlyOwner {
        if (ownerKeyRetired) revert OwnerKeyLocked();
        passkeyX = 0;
        passkeyY = 0;
        emit PasskeySet(0, 0, "");
    }

    /// @notice Lock (true) or unlock (false) the owner key. Only a passkey signature can do this, so the
    ///         key is never locked before the passkey has shown that it works.
    function setOwnerKeyRetired(bool retired) external {
        if (msg.sender != address(this)) revert OnlyViaPasskey();
        ownerKeyRetired = retired;
        emit OwnerKeyRetired(retired);
    }

    /// @notice Run one owner action that the student signed with their passkey. Anyone may submit it
    ///         (the agent relays it and pays the gas), but nobody can change it: the signature covers
    ///         this vault, this chain, the next nonce, a deadline and the exact call.
    /// @dev Not nonReentrant: the self-call below enters guarded functions such as approveRequest. The
    ///      nonce is spent before the call, so a signature can never be used twice.
    // forge-lint: disable-next-item(reentrancy-events) -- the only call before the event is a staticcall to the P-256 precompile
    function executeWithPasskey(bytes calldata data, uint64 validUntil, WebAuthn.WebAuthnAuth calldata auth)
        external
        returns (bytes memory result)
    {
        if (passkeyX == 0 && passkeyY == 0) revert NoPasskey();
        // forge-lint: disable-next-line(block-timestamp) -- deadlines are minutes long
        if (block.timestamp > validUntil) revert PasskeyExpired(validUntil);
        if (data.length < 4) revert NoCall();
        uint256 nonce = passkeyNonce;
        bytes32 digest = passkeyDigest(data, nonce, validUntil);
        if (!WebAuthn.verify(abi.encodePacked(digest), auth, passkeyX, passkeyY)) {
            revert BadPasskeySignature();
        }
        passkeyNonce = nonce + 1;
        emit PasskeyUsed(nonce, bytes4(data[:4]));
        bool ok;
        (ok, result) = address(this).call(data);
        if (!ok) {
            assembly ("memory-safe") {
                revert(add(result, 0x20), mload(result)) // pass the vault's own error through
            }
        }
    }

    /// @notice The challenge the passkey signs for owner call `data` (WebAuthn: the 32 bytes of this hash).
    function passkeyDigest(bytes calldata data, uint256 nonce, uint64 validUntil)
        public
        view
        returns (bytes32)
    {
        return keccak256(
            abi.encode(PASSKEY_DOMAIN, block.chainid, address(this), nonce, validUntil, keccak256(data))
        );
    }

    // ------------------------------------------------------------------
    // Views
    // ------------------------------------------------------------------

    function today() public view returns (uint256) {
        return block.timestamp / 1 days;
    }

    function remainingToday(address token) public view returns (uint256) {
        uint256 cap = tokenPolicy[token].dailyCap;
        uint256 spent = spentOnDay[token][today()];
        return spent >= cap ? 0 : cap - spent;
    }

    /// @notice What the agent can still pay this payee today: the payee's daily cap if set,
    ///         never more than the token's daily cap.
    function payeeRemainingToday(address payee, address token) external view returns (uint256) {
        uint256 tokenLeft = remainingToday(token);
        uint256 cap = payeeCap[payee][token];
        if (cap == 0) return tokenLeft;
        uint256 paid = paidToPayeeOnDay[payee][token][today()];
        uint256 left = paid >= cap ? 0 : cap - paid;
        return left < tokenLeft ? left : tokenLeft;
    }

    function agentActive() external view returns (bool) {
        // forge-lint: disable-next-line(block-timestamp)
        return !paused && block.timestamp <= agentExpiry;
    }

    function mandateCount() external view returns (uint256) {
        return _mandates.length;
    }

    function getMandate(uint256 id) external view returns (Mandate memory) {
        if (id >= _mandates.length) revert UnknownMandate(id);
        return _mandates[id];
    }

    function requestCount() external view returns (uint256) {
        return _requests.length;
    }

    function getRequest(uint256 id) external view returns (PaymentRequest memory) {
        if (id >= _requests.length) revert UnknownRequest(id);
        return _requests[id];
    }

    // ------------------------------------------------------------------
    // Internal
    // ------------------------------------------------------------------

    /// @dev The per-payee cap is a daily total, so splitting a payment into smaller ones cannot pass it.
    function _spendToPayee(address token, address to, uint256 amount) internal {
        uint256 cap = payeeCap[to][token];
        if (cap == 0) return;
        uint256 day = today();
        uint256 paid = paidToPayeeOnDay[to][token][day];
        if (paid + amount > cap) revert OverPayeeCap(amount, paid >= cap ? 0 : cap - paid);
        paidToPayeeOnDay[to][token][day] = paid + amount;
    }

    function _spend(address token, uint256 amount) internal {
        TokenPolicy memory p = tokenPolicy[token];
        if (!p.enabled) revert TokenNotEnabled(token);
        if (amount == 0) revert ZeroAmount();
        if (amount > p.perTxCap) revert OverPerTxCap(amount, p.perTxCap);
        uint256 day = today();
        uint256 spentAfter = spentOnDay[token][day] + amount;
        if (spentAfter > p.dailyCap) revert OverDailyCap(spentAfter, p.dailyCap);
        spentOnDay[token][day] = spentAfter;
    }

    function _pendingRequest(uint256 id) internal view returns (PaymentRequest storage r) {
        if (id >= _requests.length) revert UnknownRequest(id);
        r = _requests[id];
        if (r.status != RequestStatus.Pending) revert RequestNotPending(id);
    }
}
