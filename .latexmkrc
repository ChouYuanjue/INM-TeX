use File::Basename;
sub run_asy {
    my ($base, $dir) = fileparse($_[0]);
    return system("cd '$dir' && asy -tex pdflatex '$base'");
}
add_cus_dep("asy", "eps", 0, "run_asy");
add_cus_dep("asy", "pdf", 0, "run_asy");
add_cus_dep("asy", "tex", 0, "run_asy");

push @extra_pdflatex_options, '-synctex=1' ;
push @extra_xelatex_options, '-synctex=1' ;
push @extra_xelatex_options, '-main-memory=12000000', '-extra-mem-top=4000000', '-extra-mem-bot=4000000', '-hash-extra=200000';
push @extra_lualatex_options, '-synctex=1' ;

$max_repeat = 12;
$pdf_mode = 5;

@default_files = ('inm.tex');

# vim: ft=perl
