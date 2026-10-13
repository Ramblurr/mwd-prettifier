{ pkgs ? import <nixpkgs> {} }:
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
in pkgs.mkShell {
  packages = [ pkgs.python3 pkgs.uv pkgs.dict kindleunpack ];
}
