// Jest transform: the mobile app ships TypeScript with .ts-extended import
// specifiers; preset-env at the current Node target plus type-stripping
// covers the pure protocol/store logic these tests exercise.
module.exports = {
  presets: [
    ["@babel/preset-env", { targets: { node: "current" } }],
    "@babel/preset-typescript",
  ],
};
