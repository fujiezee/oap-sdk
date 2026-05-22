// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/**
 * MockTEEVerifier — 测试用 TEE 验证器（总是返回 true）
 */
contract MockTEEVerifier {
    function verifyAttestation(
        bytes32, /* taskId */
        bytes calldata, /* attestationReport */
        bytes32 /* expectedMrEnclave */
    ) external pure returns (bool) {
        return true; // 测试中总是验证通过
    }
}

/**
 * MockZKVerifier — 测试用 ZK 证明验证器（总是返回 true）
 */
contract MockZKVerifier {
    function verifyProof(
        bytes32, /* taskId */
        bytes32, /* resultHash */
        bytes calldata /* proof */
    ) external pure returns (bool) {
        return true; // 测试中总是验证通过
    }
}
