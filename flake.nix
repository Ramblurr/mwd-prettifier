{
  description = "MW Collegiate prettifier development environment";
  inputs = {
    nixpkgs.url = "https://flakehub.com/f/NixOS/nixpkgs/0.1"; # tracks nixpkgs unstable branch
    devshell.url = "github:numtide/devshell";
    devshell.inputs.nixpkgs.follows = "nixpkgs";
    devenv.url = "https://flakehub.com/f/ramblurr/nix-devenv/*";
    devenv.inputs.nixpkgs.follows = "nixpkgs";
  };
  outputs =
    inputs@{
      self,
      devenv,
      devshell,
      ...
    }:
    devenv.lib.mkFlake ./. {
      inherit inputs;
      withOverlays = [
        devshell.overlays.default
        devenv.overlays.default
      ];
      devShell =
        pkgs:
        let
          kindleUnpackSource = pkgs.fetchFromGitHub {
            owner = "kevinhendricks";
            repo = "KindleUnpack";
            rev = "bf0ca6ece4e73494625e7950be3e259b6260774c";
            sha256 = "1yfvjby1lk9az7mby0innpi0lnb2wlyhyx6rm85ji36qb44laqw0";
          };
          kindleunpack = pkgs.writeShellScriptBin "kindleunpack" ''
            exec ${pkgs.python3}/bin/python3 ${kindleUnpackSource}/lib/kindleunpack.py "$@"
          '';
        in
        pkgs.devshell.mkShell {
          imports = [
            devenv.capsules.base
          ];
          commands = [
            {
              package = kindleunpack;
              help = "Recover publisher XHTML from an unencrypted MOBI";
            }
          ];
          packages = [
            pkgs.python3
            pkgs.uv
            pkgs.dict
          ];
        };
    };
}
