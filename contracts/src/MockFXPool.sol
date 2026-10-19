// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @title MockFXPool
/// @notice Test-only fixed-rate converter between a USD and a HKD stablecoin.
/// @dev Both tokens use 6 decimals. Rate is HKD per 1 USD scaled by 1e6 (7.80 => 7_800_000).
///      The owner sets the rate; in production this would be a licensed FX provider or oracle.
contract MockFXPool is Ownable {
    using SafeERC20 for IERC20;

    uint256 public constant RATE_SCALE = 1e6;
    uint256 public constant BPS = 10_000;
    uint256 public constant MAX_FEE_BPS = 500;

    IERC20 public immutable usd;
    IERC20 public immutable hkd;
    uint256 public hkdPerUsd;
    uint256 public feeBps;

    event RateSet(uint256 hkdPerUsd, uint256 feeBps);
    event Swapped(
        address indexed sender, address indexed tokenIn, uint256 amountIn, uint256 amountOut, address to
    );

    error UnsupportedToken(address token);
    error InvalidRate();
    error Slippage(uint256 amountOut, uint256 minOut);

    constructor(IERC20 usd_, IERC20 hkd_, uint256 hkdPerUsd_, uint256 feeBps_) Ownable(msg.sender) {
        usd = usd_;
        hkd = hkd_;
        _setRate(hkdPerUsd_, feeBps_);
    }

    function setRate(uint256 hkdPerUsd_, uint256 feeBps_) external onlyOwner {
        _setRate(hkdPerUsd_, feeBps_);
    }

    /// @notice The token you receive when you send `tokenIn`.
    function tokenOutFor(address tokenIn) public view returns (address) {
        if (tokenIn == address(usd)) return address(hkd);
        if (tokenIn == address(hkd)) return address(usd);
        revert UnsupportedToken(tokenIn);
    }

    /// @notice Amount out after the fee, for `amountIn` of `tokenIn`.
    function quote(address tokenIn, uint256 amountIn) public view returns (uint256) {
        uint256 gross = tokenIn == address(usd)
            ? (amountIn * hkdPerUsd) / RATE_SCALE
            : tokenIn == address(hkd) ? (amountIn * RATE_SCALE) / hkdPerUsd : _unsupported(tokenIn);
        return (gross * (BPS - feeBps)) / BPS;
    }

    function swap(address tokenIn, uint256 amountIn, uint256 minOut, address to)
        external
        returns (uint256 amountOut)
    {
        address tokenOut = tokenOutFor(tokenIn);
        amountOut = quote(tokenIn, amountIn);
        if (amountOut < minOut) revert Slippage(amountOut, minOut);
        IERC20(tokenIn).safeTransferFrom(msg.sender, address(this), amountIn);
        IERC20(tokenOut).safeTransfer(to, amountOut);
        emit Swapped(msg.sender, tokenIn, amountIn, amountOut, to);
    }

    function _setRate(uint256 hkdPerUsd_, uint256 feeBps_) internal {
        if (hkdPerUsd_ == 0 || feeBps_ > MAX_FEE_BPS) revert InvalidRate();
        hkdPerUsd = hkdPerUsd_;
        feeBps = feeBps_;
        emit RateSet(hkdPerUsd_, feeBps_);
    }

    function _unsupported(address token) private pure returns (uint256) {
        revert UnsupportedToken(token);
    }
}
