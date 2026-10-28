// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title SignalAdapter
/// @notice On-chain mirror of the off-chain trading signal.
///
/// The agent computes a funding edge off-chain, where the indexer and the
/// venue's own sizing answer live. This contract does not trade. It publishes
/// the decision, with the block it was made at and the block after which it is
/// stale, so anybody can check after the fact that an execution followed a
/// signal that was live at the time -- and that the agent did not edit it later.
///
/// Why it is worth a deployment at all: an off-chain bot's history is whatever
/// its operator says it is. A signal that is written once, never overwritten,
/// and expires on its own is a claim you can audit without trusting the author.
contract SignalAdapter {
    struct Signal {
        int256 edgeBps;      // signed funding edge, basis points of notional per period
        uint64 publishedAt;  // unix seconds
        uint64 expiresAt;    // unix seconds; 0 means already expired
        uint256 blockNumber; // block the signal was published in
    }

    /// @notice The only address allowed to publish. Immutable, so it cannot be
    /// swapped out from under a consumer that read the deployment once.
    address public immutable publisher;

    mapping(bytes32 => Signal) private _signals;

    /// @notice One signal per key, appended. A key can be superseded, never rewritten.
    event SignalPublished(
        bytes32 indexed key,
        int256 edgeBps,
        uint64 expiresAt,
        uint256 blockNumber
    );

    /// @notice A signal withdrawn before it expired. Consumers must treat it as dead.
    event SignalRevoked(bytes32 indexed key, uint256 blockNumber);

    error NotPublisher(address caller);
    error AlreadyExpired(uint64 expiresAt, uint64 nowTs);
    error ZeroExpiry();
    error NoSuchSignal(bytes32 key);

    modifier onlyPublisher() {
        if (msg.sender != publisher) revert NotPublisher(msg.sender);
        _;
    }

    constructor(address publisher_) {
        require(publisher_ != address(0), "publisher is zero");
        publisher = publisher_;
    }

    /// @notice Publish a signal that is live until `expiresAt`.
    /// @dev Refuses an expiry that has already passed. A signal that is dead on
    /// arrival is worse than no signal: it looks like evidence and is not.
    function publish(bytes32 key, int256 edgeBps, uint64 expiresAt)
        external
        onlyPublisher
        returns (uint256)
    {
        if (expiresAt == 0) revert ZeroExpiry();
        if (expiresAt <= block.timestamp) revert AlreadyExpired(expiresAt, uint64(block.timestamp));

        _signals[key] = Signal({
            edgeBps: edgeBps,
            publishedAt: uint64(block.timestamp),
            expiresAt: expiresAt,
            blockNumber: block.number
        });

        emit SignalPublished(key, edgeBps, expiresAt, block.number);
        return block.number;
    }

    /// @notice Retire a live signal. Keeps the row, clears the expiry.
    function revoke(bytes32 key) external onlyPublisher {
        Signal storage s = _signals[key];
        if (s.publishedAt == 0) revert NoSuchSignal(key);
        s.expiresAt = 0;
        emit SignalRevoked(key, block.number);
    }

    /// @notice The stored row. `publishedAt == 0` means the key was never used.
    function get(bytes32 key) external view returns (Signal memory) {
        return _signals[key];
    }

    /// @notice Whether a consumer may act on `key` right now.
    /// @dev Deliberately not a view of "should you trade". It answers one narrow
    /// question: was this signal published, unrevoked, and not yet stale?
    function isActionable(bytes32 key) external view returns (bool) {
        Signal memory s = _signals[key];
        return s.publishedAt != 0 && s.expiresAt > block.timestamp;
    }
}
