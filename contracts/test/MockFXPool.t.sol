// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {MockStablecoin} from "../src/MockStablecoin.sol";
import {MockFXPool} from "../src/MockFXPool.sol";

contract MockFXPoolTest is Test {
    MockStablecoin internal hkd;
    MockStablecoin internal usd;
    MockFXPool internal pool;
    address internal alice = makeAddr("alice");

    function setUp() public {
        hkd = new MockStablecoin("Test HKD Stablecoin", "tHKD");
        usd = new MockStablecoin("Test USD Stablecoin", "tUSD");
        pool = new MockFXPool(usd, hkd, 7_800_000, 10);
        hkd.mint(address(pool), 1_000_000e6);
        usd.mint(address(pool), 100_000e6);
    }

    function test_QuoteBothDirections() public view {
        assertEq(pool.quote(address(usd), 100e6), 779_220_000); // 780 HKD minus 0.10%
        assertEq(pool.quote(address(hkd), 780e6), 99_900_000); // 100 USD minus 0.10%
    }

    function test_SwapPullsAndPays() public {
        usd.mint(alice, 100e6);
        vm.startPrank(alice);
        usd.approve(address(pool), 100e6);
        uint256 out = pool.swap(address(usd), 100e6, 0, alice);
        vm.stopPrank();
        assertEq(out, 779_220_000);
        assertEq(hkd.balanceOf(alice), out);
        assertEq(usd.balanceOf(alice), 0);
    }

    function test_RevertWhen_UnsupportedToken() public {
        MockStablecoin other = new MockStablecoin("Other", "OTH");
        vm.expectRevert(abi.encodeWithSelector(MockFXPool.UnsupportedToken.selector, address(other)));
        pool.quote(address(other), 1);
    }

    function test_RevertWhen_NonOwnerSetsRate() public {
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, alice));
        pool.setRate(1, 0);
    }

    function test_RevertWhen_InvalidRate() public {
        vm.expectRevert(MockFXPool.InvalidRate.selector);
        pool.setRate(0, 10);
        vm.expectRevert(MockFXPool.InvalidRate.selector);
        pool.setRate(7_800_000, 501);
    }
}
