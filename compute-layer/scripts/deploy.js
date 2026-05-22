/**
 * AvatarComputeMarket 部署脚本
 *
 * 用法：
 *   本地测试网：  npx hardhat run scripts/deploy.js --network localhost
 *   Sepolia：     npx hardhat run scripts/deploy.js --network sepolia
 *
 * 环境变量（Sepolia 部署）：
 *   DEPLOYER_PRIVATE_KEY   部署者私钥
 *   SEPOLIA_RPC_URL        Sepolia RPC 节点 URL
 *   TEE_VERIFIER_ADDRESS   已部署的 TEE 验证器地址（可选，未提供时部署 Mock）
 *   ZK_VERIFIER_ADDRESS    已部署的 ZK 验证器地址（可选，未提供时部署 Mock）
 */

const { ethers } = require("hardhat");

async function main() {
  const [deployer] = await ethers.getSigners();
  const network = await ethers.provider.getNetwork();

  console.log("\n" + "═".repeat(60));
  console.log("  OAP AvatarComputeMarket 部署");
  console.log("═".repeat(60));
  console.log(`  网络:    ${network.name} (chainId: ${network.chainId})`);
  console.log(`  部署者:  ${deployer.address}`);

  const balance = await ethers.provider.getBalance(deployer.address);
  console.log(`  余额:    ${ethers.formatEther(balance)} ETH`);
  console.log("─".repeat(60));

  // ── Step 1: 部署验证器合约 ────────────────────────────

  let teeVerifierAddress = process.env.TEE_VERIFIER_ADDRESS;
  let zkVerifierAddress  = process.env.ZK_VERIFIER_ADDRESS;

  if (!teeVerifierAddress) {
    console.log("\n  [1/3] 部署 MockTEEVerifier（测试用）...");
    const MockTEEVerifier = await ethers.deployContract("MockTEEVerifier");
    await MockTEEVerifier.waitForDeployment();
    teeVerifierAddress = await MockTEEVerifier.getAddress();
    console.log(`        ✓ MockTEEVerifier: ${teeVerifierAddress}`);
  } else {
    console.log(`  [1/3] 使用已有 TEEVerifier: ${teeVerifierAddress}`);
  }

  if (!zkVerifierAddress) {
    console.log("\n  [2/3] 部署 MockZKVerifier（测试用）...");
    const MockZKVerifier = await ethers.deployContract("MockZKVerifier");
    await MockZKVerifier.waitForDeployment();
    zkVerifierAddress = await MockZKVerifier.getAddress();
    console.log(`        ✓ MockZKVerifier:  ${zkVerifierAddress}`);
  } else {
    console.log(`  [2/3] 使用已有 ZKVerifier: ${zkVerifierAddress}`);
  }

  // ── Step 2: 部署主合约 ────────────────────────────────

  console.log("\n  [3/3] 部署 AvatarComputeMarket...");
  const Market = await ethers.getContractFactory("AvatarComputeMarket");
  const market = await Market.deploy(teeVerifierAddress, zkVerifierAddress);
  await market.waitForDeployment();
  const marketAddress = await market.getAddress();
  console.log(`        ✓ AvatarComputeMarket: ${marketAddress}`);

  // ── Step 3: 验证部署 ──────────────────────────────────

  const minStake = await market.minimumStake();
  const bidPeriod = await market.bidPeriod();

  console.log("\n  合约参数验证:");
  console.log(`    最小质押: ${ethers.formatEther(minStake)} ETH`);
  console.log(`    投标时间: ${bidPeriod}s`);

  // ── Step 4: 保存部署信息 ──────────────────────────────

  const deploymentInfo = {
    network: network.name,
    chainId: network.chainId.toString(),
    deployer: deployer.address,
    timestamp: new Date().toISOString(),
    contracts: {
      AvatarComputeMarket: marketAddress,
      TEEVerifier: teeVerifierAddress,
      ZKVerifier: zkVerifierAddress,
    },
    config: {
      minimumStake: minStake.toString(),
      bidPeriod: bidPeriod.toString(),
    },
  };

  const { writeFileSync } = require("fs");
  const outPath = `./deployments/${network.name}-${Date.now()}.json`;

  try {
    require("fs").mkdirSync("./deployments", { recursive: true });
    writeFileSync(outPath, JSON.stringify(deploymentInfo, null, 2));
    console.log(`\n  部署信息已保存: ${outPath}`);
  } catch (e) {
    console.warn(`  无法保存部署信息: ${e.message}`);
  }

  // ── 完成 ──────────────────────────────────────────────

  console.log("\n" + "═".repeat(60));
  console.log("  部署完成！");
  console.log("\n  下一步：");
  console.log(`    1. 将合约地址写入 .env 文件:`);
  console.log(`       MARKET_ADDRESS=${marketAddress}`);
  console.log(`    2. 启动算力节点并注册:`);
  console.log(`       npx hardhat run scripts/register_node.js --network ${network.name}`);
  if (network.name !== "hardhat" && network.name !== "localhost") {
    console.log(`    3. 在 Etherscan 验证合约:`);
    console.log(`       npx hardhat verify --network ${network.name} ${marketAddress} \\`);
    console.log(`         ${teeVerifierAddress} ${zkVerifierAddress}`);
  }
  console.log("═".repeat(60) + "\n");
}

main().catch((error) => {
  console.error("\n部署失败:", error);
  process.exit(1);
});
